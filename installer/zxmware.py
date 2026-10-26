#!/usr/bin/env python3
"""
zxmware.py -- companion CLI installer/updater for the self-hosted ZxmWare
Flipper Zero firmware/app distribution system.

This tool talks to:
  * a Flipper Zero over its USB CDC serial CLI, and
  * a self-hosted ZxmWare HTTP server.

Server API (built by a separate component):
  GET /firmware/directory.json
      {"name","generated","channels":{"release":[{version,file,path,size,
       sha256,target,info}], "dev":[...]}}
  GET /firmware/<file>.tgz
  GET /apps/directory.json
      {"name","generated","categories":{"<Cat>":[{id,name,category,file,
       path,size,sha256}]}}
  GET /apps/<Category>/<name>.fap
  GET /health

Subcommands:
  detect            list candidate Flipper serial ports and print device info
  sync              fetch + cache both directory.json files
  list-apps         show available apps from the synced catalog
  install-app       download a .fap (verify sha256) and push to the Flipper
  list-firmware     show available firmware bundles from the synced catalog
  update-firmware   download/verify/extract a firmware bundle and flash it
  free              show SD free space

------------------------------------------------------------------------------
IMPORTANT, HONEST CAVEAT ABOUT SERIAL FILE UPLOAD
------------------------------------------------------------------------------
Uploading files to the Flipper over the *text* CLI (`storage write`) is
inherently finicky. The Flipper's `storage write` command reads raw bytes from
the serial stream until it sees a Ctrl-C (0x03), but there is no robust framing,
flow control, or acknowledgement in the plain text protocol. Bytes equal to 0x03
inside a binary file would prematurely terminate the write, so this tool works
around that by chunking and (where the firmware supports it) using the newer
`storage write_chunk <path> <size>` command which takes an explicit byte count
and therefore does NOT need a Ctrl-C terminator and is binary-safe.

Even so, large transfers over the CLI are slow and occasionally unreliable
depending on firmware version and host USB stack. For anything large -- and for
firmware flashing in particular -- qFlipper (official GUI), `ufbt`, or
`pyflipper` are the fully-supported, reliable paths. This tool implements the
CLI upload best-effort and loudly points you at those tools when appropriate.
------------------------------------------------------------------------------
"""

import argparse
import hashlib
import json
import logging
import os
import sys
import tarfile
import tempfile
import time
from pathlib import Path

# --- Third-party deps (see requirements.txt) --------------------------------
# pyserial is required for all device operations. We import lazily-ish so that
# purely network/catalog subcommands (sync, list-*) still work on a machine
# without pyserial installed.
try:
    import serial  # type: ignore
    from serial.tools import list_ports  # type: ignore
    HAVE_PYSERIAL = True
except ImportError:  # pragma: no cover - environment dependent
    serial = None
    list_ports = None
    HAVE_PYSERIAL = False

# `requests` is optional; we fall back to urllib from the stdlib if it is not
# installed, so the tool degrades gracefully.
try:
    import requests  # type: ignore
    HAVE_REQUESTS = True
except ImportError:  # pragma: no cover - environment dependent
    requests = None
    HAVE_REQUESTS = False

import urllib.request
import urllib.error


# -----------------------------------------------------------------------------
# Constants / configuration
# -----------------------------------------------------------------------------

# Flipper Zero USB CDC identifiers (STMicroelectronics Virtual COM Port).
FLIPPER_VID = 0x0483
FLIPPER_PID = 0x5740

# The Flipper CLI prints this prompt when it is ready for the next command.
PROMPT = b">: "

# Default server, overridable via env or --server.
DEFAULT_SERVER = os.environ.get("ZXMWARE_SERVER", "http://localhost:8000")

# Local cache directory for catalog files.
CACHE_DIR = Path.home() / ".zxmware" / "cache"

# Where firmware is staged on the Flipper SD card before `update install`.
FIRMWARE_UPDATE_DIR = "/ext/update/ZxmWare"

# Where apps live on the SD card.
APPS_ROOT = "/ext/apps"

# Serial read timeout (seconds) for each blocking read.
SERIAL_TIMEOUT = 2.0

# Upload chunk size in bytes. Kept modest to stay friendly to the CLI buffer.
UPLOAD_CHUNK = 4096


log = logging.getLogger("zxmware")


# -----------------------------------------------------------------------------
# Helpers: hashing, HTTP, catalog cache
# -----------------------------------------------------------------------------

def sha256_file(path):
    """Return the hex sha256 digest of a file on disk."""
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(65536), b""):
            h.update(block)
    return h.hexdigest()


def sha256_bytes(data):
    """Return the hex sha256 digest of a bytes object."""
    return hashlib.sha256(data).hexdigest()


def http_get_bytes(url, timeout=60):
    """
    GET a URL and return the raw response body as bytes.

    Uses `requests` when available, otherwise falls back to urllib. Raises on
    any non-success status so callers can fail loudly.
    """
    log.debug("HTTP GET %s", url)
    if HAVE_REQUESTS:
        resp = requests.get(url, timeout=timeout)
        resp.raise_for_status()
        return resp.content
    # stdlib fallback
    req = urllib.request.Request(url, headers={"User-Agent": "zxmware-cli"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read()
    except urllib.error.HTTPError as e:  # pragma: no cover - network dependent
        raise RuntimeError("HTTP %s for %s" % (e.code, url)) from e
    except urllib.error.URLError as e:  # pragma: no cover - network dependent
        raise RuntimeError("Could not reach %s: %s" % (url, e.reason)) from e


def http_get_json(url, timeout=30):
    """GET a URL and parse the body as JSON."""
    return json.loads(http_get_bytes(url, timeout=timeout).decode("utf-8"))


def cache_path(name):
    """Return the local cache file path for a catalog name."""
    return CACHE_DIR / name


def load_cached_json(name):
    """
    Load a previously-synced catalog from the cache. Returns None (and logs a
    helpful hint) if it has not been synced yet.
    """
    p = cache_path(name)
    if not p.exists():
        log.error("No cached %s found. Run `zxmware sync` first.", name)
        return None
    with open(p, "r", encoding="utf-8") as fh:
        return json.load(fh)


# -----------------------------------------------------------------------------
# FlipperCLI: a small wrapper around the Flipper Zero serial CLI
# -----------------------------------------------------------------------------

class FlipperError(RuntimeError):
    """Raised for any Flipper serial interaction failure."""


class FlipperCLI:
    """
    Thin wrapper around the Flipper Zero's text CLI over USB CDC serial.

    Usage:
        with FlipperCLI(port) as flip:
            print(flip.send("device_info"))

    The CLI is line/prompt oriented: after each command the device echoes the
    command and then streams output, finishing with the prompt ">: ". We read
    until we see that prompt (or time out).
    """

    def __init__(self, port, baudrate=115200, timeout=SERIAL_TIMEOUT):
        if not HAVE_PYSERIAL:
            raise FlipperError(
                "pyserial is not installed. Install it with "
                "`pip install -r requirements.txt` to talk to the device."
            )
        self.port = port
        self.baudrate = baudrate
        self.timeout = timeout
        self.ser = None

    # -- context manager plumbing -------------------------------------------
    def __enter__(self):
        self.open()
        return self

    def __exit__(self, exc_type, exc, tb):
        self.close()

    def open(self):
        """Open the serial port and get to a clean prompt."""
        if self.port is None:
            raise FlipperError(
                "No Flipper serial port specified or auto-detected. "
                "Connect the device and/or pass --port."
            )
        log.info("Opening serial port %s @ %d baud", self.port, self.baudrate)
        try:
            self.ser = serial.Serial(
                self.port, self.baudrate, timeout=self.timeout
            )
        except serial.SerialException as e:
            raise FlipperError(
                "Could not open %s: %s" % (self.port, e)
            ) from e
        # Give the CDC link a moment and clear any boot banner, then sync to a
        # known prompt by sending a newline.
        time.sleep(0.2)
        self.ser.reset_input_buffer()
        self._write(b"\r\n")
        self._read_until_prompt(quiet=True)

    def close(self):
        """Close the serial port if open."""
        if self.ser is not None and self.ser.is_open:
            log.debug("Closing serial port %s", self.port)
            self.ser.close()
        self.ser = None

    # -- low-level IO --------------------------------------------------------
    def _write(self, data):
        """Write raw bytes to the port."""
        if self.ser is None:
            raise FlipperError("Serial port is not open.")
        self.ser.write(data)
        self.ser.flush()

    def _read_until_prompt(self, extra_timeout=10.0, quiet=False):
        """
        Read bytes until the device prints the CLI prompt, or until we go
        `extra_timeout` seconds without any new data. Returns the accumulated
        bytes (prompt stripped).
        """
        buf = bytearray()
        deadline = time.time() + extra_timeout
        while True:
            chunk = self.ser.read(256)
            if chunk:
                buf.extend(chunk)
                deadline = time.time() + extra_timeout  # reset idle timer
                if buf.endswith(PROMPT):
                    break
            else:
                # No data this cycle; check the idle deadline.
                if time.time() > deadline:
                    if not quiet:
                        log.warning(
                            "Timed out waiting for prompt (got %d bytes).",
                            len(buf),
                        )
                    break
        # Strip a trailing prompt if present.
        if buf.endswith(PROMPT):
            buf = buf[: -len(PROMPT)]
        return bytes(buf)

    # -- command helpers -----------------------------------------------------
    def send(self, command, extra_timeout=10.0):
        """
        Send a CLI command and return its textual output with the echoed
        command line and trailing prompt removed.
        """
        log.debug("CLI >> %s", command)
        self.ser.reset_input_buffer()
        self._write(command.encode("utf-8") + b"\r\n")
        raw = self._read_until_prompt(extra_timeout=extra_timeout)
        text = raw.decode("utf-8", errors="replace")
        # The device echoes the command back as the first line; drop it.
        lines = text.splitlines()
        if lines and command.strip() in lines[0]:
            lines = lines[1:]
        out = "\n".join(lines).strip("\r\n")
        log.debug("CLI << %s", out.replace("\n", "\\n"))
        return out

    def device_info(self):
        """
        Return device info text. Newer firmware uses `device_info`; some older
        builds use `info`. Try both.
        """
        out = self.send("device_info")
        if not out or "command not found" in out.lower():
            out = self.send("info")
        return out

    def storage_info(self, path="/ext"):
        """Return the raw output of `storage info <path>` (free space, etc.)."""
        return self.send("storage info %s" % path)

    def storage_list(self, path="/ext"):
        """Return the raw output of `storage list <path>`."""
        return self.send("storage list %s" % path)

    def storage_md5(self, path):
        """
        Return the md5 digest the device reports for a file, or None if the
        command failed. (The Flipper CLI exposes md5, not sha256, so this is
        used for a best-effort on-device integrity check only -- the mandatory
        verification is the sha256 we do on the downloaded bytes.)
        """
        out = self.send("storage md5 %s" % path)
        # Output is typically just the hex digest; guard against error text.
        token = out.strip().split()
        if len(token) == 1 and len(token[0]) == 32:
            return token[0].lower()
        log.debug("Unexpected md5 output for %s: %r", path, out)
        return None

    def mkdir_p(self, path):
        """
        Create `path` and all missing parents on the SD card. The Flipper has
        no `mkdir -p`, so we create each path component in turn and ignore
        "already exists" style errors.
        """
        parts = [p for p in path.strip("/").split("/") if p]
        cur = ""
        for part in parts:
            cur = cur + "/" + part
            # Skip the SD root itself; it always exists.
            if cur in ("/ext", "/int"):
                continue
            out = self.send("storage mkdir %s" % cur)
            low = out.lower()
            if out and "exist" not in low and "error" not in low:
                log.debug("mkdir %s -> %s", cur, out.strip())
            elif "error" in low and "exist" not in low:
                # Non-fatal: directory may already exist under another message.
                log.debug("mkdir %s reported: %s", cur, out.strip())

    def remove(self, path):
        """Remove a file or (empty) directory on the SD card."""
        return self.send("storage remove %s" % path)

    # -- file upload ---------------------------------------------------------
    def storage_write(self, dest_path, data):
        """
        Upload `data` (bytes) to `dest_path` on the SD card.

        Strategy:
          1. Prefer `storage write_chunk <path> <size>`: the device reads
             exactly <size> bytes, so it is binary-safe (no Ctrl-C terminator,
             no problem with 0x03 bytes in the payload). This is available on
             reasonably recent firmware.
          2. Fall back to classic `storage write <path>`: stream the bytes then
             send Ctrl-C (0x03) to terminate. This is NOT binary-safe if the
             payload itself contains 0x03, and is the finicky path the module
             docstring warns about.

        Returns True on apparent success. Callers should still verify integrity
        (we do, via md5/sha256) rather than trusting the transfer blindly.
        """
        size = len(data)
        log.info("Uploading %d bytes -> %s", size, dest_path)

        # --- Attempt 1: write_chunk (binary-safe) --------------------------
        # We probe support by issuing the command; if the firmware does not
        # know it, it replies with a "command not found" style message and we
        # fall back.
        self.ser.reset_input_buffer()
        self._write(("storage write_chunk %s %d\r\n" % (dest_path, size)).encode())
        # The device prints a short "Ready" / prompt-ish acknowledgement before
        # reading bytes. Give it a brief moment and inspect what came back.
        time.sleep(0.3)
        ack = self.ser.read(256).decode("utf-8", errors="replace").lower()
        if "command not found" in ack or "usage" in ack and "write_chunk" in ack:
            log.debug("Firmware lacks write_chunk; using classic storage write.")
            # Drain to prompt and fall through to the classic path.
            self._read_until_prompt(quiet=True)
            return self._storage_write_classic(dest_path, data)

        # Stream the payload in chunks. write_chunk reads exactly `size` bytes.
        self._stream_bytes(data)
        out = self._read_until_prompt(extra_timeout=15.0)
        low = out.decode("utf-8", errors="replace").lower()
        if "error" in low:
            raise FlipperError(
                "storage write_chunk reported an error for %s: %s"
                % (dest_path, out.decode("utf-8", errors="replace"))
            )
        return True

    def _storage_write_classic(self, dest_path, data):
        """
        Classic `storage write` upload path.

        NOTE / LIMITATION: this terminates the transfer with Ctrl-C (0x03). If
        the file contains a 0x03 byte, the device will stop early and the file
        will be truncated. We detect that risk and warn; integrity is verified
        afterwards by the caller. For binary .fap / firmware payloads prefer a
        firmware that supports write_chunk, or use qFlipper.
        """
        if b"\x03" in data:
            log.warning(
                "Payload contains 0x03 bytes; classic `storage write` may "
                "truncate it. qFlipper is recommended for this file."
            )
        self.ser.reset_input_buffer()
        self._write(("storage write %s\r\n" % dest_path).encode())
        time.sleep(0.3)
        # Device prints instructions; ignore them and stream the bytes.
        self.ser.read(256)
        self._stream_bytes(data)
        # Terminate the write with Ctrl-C.
        self._write(b"\x03")
        out = self._read_until_prompt(extra_timeout=15.0)
        low = out.decode("utf-8", errors="replace").lower()
        if "error" in low:
            raise FlipperError(
                "storage write reported an error for %s: %s"
                % (dest_path, out.decode("utf-8", errors="replace"))
            )
        return True

    def _stream_bytes(self, data):
        """Write bytes to the port in modest chunks with small pauses."""
        view = memoryview(data)
        total = len(data)
        sent = 0
        for off in range(0, total, UPLOAD_CHUNK):
            chunk = view[off:off + UPLOAD_CHUNK]
            self.ser.write(chunk)
            self.ser.flush()
            sent += len(chunk)
            # A tiny pause lets the device's storage keep up on slower cards.
            time.sleep(0.01)
            log.debug("  streamed %d/%d bytes", sent, total)


# -----------------------------------------------------------------------------
# Port detection
# -----------------------------------------------------------------------------

def find_flipper_ports():
    """
    Return a list of (device, description) tuples for likely Flipper ports.

    We match primarily on the STMicro VID/PID, then fall back to name hints
    (macOS names the port /dev/tty.usbmodemflip_*; Linux uses /dev/ttyACM*).
    """
    if not HAVE_PYSERIAL:
        log.error("pyserial not installed; cannot enumerate serial ports.")
        return []
    candidates = []
    for p in list_ports.comports():
        hit = False
        if p.vid == FLIPPER_VID and p.pid == FLIPPER_PID:
            hit = True
        name_blob = " ".join(
            str(x) for x in (p.device, p.description, p.product, p.manufacturer)
            if x
        ).lower()
        if "flip" in name_blob or "stmicro" in name_blob:
            hit = True
        if hit:
            candidates.append((p.device, p.description or "unknown"))
    return candidates


def resolve_port(explicit):
    """
    Resolve which serial port to use: an explicit --port wins; otherwise
    auto-detect and use the single candidate. Returns the device string or
    None.
    """
    if explicit:
        return explicit
    ports = find_flipper_ports()
    if not ports:
        log.error(
            "No Flipper serial port auto-detected. Plug in the device and "
            "unlock it, or pass --port explicitly (e.g. "
            "/dev/tty.usbmodemflip_XXXX)."
        )
        return None
    if len(ports) > 1:
        log.warning(
            "Multiple candidate ports found: %s. Using the first; pass --port "
            "to choose.", ", ".join(d for d, _ in ports)
        )
    log.info("Auto-detected Flipper port: %s", ports[0][0])
    return ports[0][0]


# -----------------------------------------------------------------------------
# Subcommand implementations
# -----------------------------------------------------------------------------

def cmd_detect(args):
    """List candidate Flipper ports and print device info for one of them."""
    ports = find_flipper_ports()
    if not ports:
        print("No candidate Flipper Zero serial ports found.")
        print("Hints:")
        print("  * macOS:   /dev/tty.usbmodemflip_*")
        print("  * Linux:   /dev/ttyACM*   (you may need dialout group perms)")
        print("  * Windows: COMx")
        return 1
    print("Candidate Flipper Zero ports:")
    for dev, desc in ports:
        print("  %s  (%s)" % (dev, desc))
    # Try to read device info from the chosen/first port.
    port = resolve_port(args.port)
    if not port:
        return 0
    try:
        with FlipperCLI(port) as flip:
            print("\nDevice info from %s:\n" % port)
            print(flip.device_info())
    except FlipperError as e:
        log.error("Could not query device info: %s", e)
        return 1
    return 0


def cmd_sync(args):
    """Fetch both directory.json files from the server and cache them."""
    server = args.server.rstrip("/")
    CACHE_DIR.mkdir(parents=True, exist_ok=True)

    # Friendly health check first so failures are obvious.
    try:
        http_get_bytes(server + "/health", timeout=10)
        log.info("Server %s is healthy.", server)
    except Exception as e:  # noqa: BLE001 - we want to report any failure
        log.warning("Health check failed for %s: %s", server, e)

    ok = True
    for name, url in (
        ("firmware-directory.json", server + "/firmware/directory.json"),
        ("apps-directory.json", server + "/apps/directory.json"),
    ):
        try:
            data = http_get_bytes(url)
            # Validate it parses as JSON before caching.
            json.loads(data.decode("utf-8"))
            out = cache_path(name)
            with open(out, "wb") as fh:
                fh.write(data)
            log.info("Cached %s (%d bytes) -> %s", name, len(data), out)
        except Exception as e:  # noqa: BLE001
            log.error("Failed to sync %s: %s", url, e)
            ok = False
    if ok:
        print("Sync complete. Catalogs cached in %s" % CACHE_DIR)
    return 0 if ok else 1


def _iter_apps(catalog):
    """Yield every app entry across all categories in the apps catalog."""
    for cat, entries in (catalog.get("categories") or {}).items():
        for entry in entries:
            yield cat, entry


def cmd_list_apps(args):
    """Show apps from the synced catalog, optionally filtered by category."""
    catalog = load_cached_json("apps-directory.json")
    if catalog is None:
        return 1
    rows = []
    for cat, entry in _iter_apps(catalog):
        if args.category and cat.lower() != args.category.lower():
            continue
        rows.append((cat, entry.get("id", "?"), entry.get("name", "?"),
                     entry.get("size", 0)))
    if not rows:
        print("No apps found%s." %
              ((" in category %r" % args.category) if args.category else ""))
        return 0
    rows.sort()
    print("%-14s %-22s %-28s %10s" % ("CATEGORY", "ID", "NAME", "SIZE"))
    print("-" * 78)
    for cat, aid, name, size in rows:
        print("%-14s %-22s %-28s %10d" % (cat, aid, name, size))
    print("\n%d app(s)." % len(rows))
    return 0


def _find_app(catalog, needle):
    """
    Find an app in the catalog by exact id, then by case-insensitive name.
    Returns (category, entry) or (None, None).
    """
    for cat, entry in _iter_apps(catalog):
        if entry.get("id") == needle:
            return cat, entry
    for cat, entry in _iter_apps(catalog):
        if entry.get("name", "").lower() == needle.lower():
            return cat, entry
    return None, None


def cmd_install_app(args):
    """Download a .fap, verify its sha256, and push it to the Flipper."""
    catalog = load_cached_json("apps-directory.json")
    if catalog is None:
        return 1
    cat, entry = _find_app(catalog, args.target)
    if entry is None:
        log.error("App %r not found in catalog. Try `zxmware list-apps`.",
                  args.target)
        return 1

    server = args.server.rstrip("/")
    # The catalog gives a server-relative path; prefer it, else build one.
    rel = entry.get("path") or ("/apps/%s/%s" % (cat, entry["file"]))
    url = server + rel
    expected = entry.get("sha256", "").lower()

    log.info("Downloading app %s (%s) from %s", entry.get("name"), entry.get("id"), url)
    data = http_get_bytes(url)

    # --- mandatory sha256 verification ---------------------------------
    actual = sha256_bytes(data)
    if not expected:
        log.error("Catalog entry has no sha256; refusing to install blindly.")
        return 1
    if actual != expected:
        log.error("SHA256 MISMATCH for %s!\n  expected %s\n  got      %s",
                  entry.get("file"), expected, actual)
        log.error("Refusing to install a file that failed verification.")
        return 1
    log.info("SHA256 verified OK (%s).", actual)

    # --- push to device -------------------------------------------------
    port = resolve_port(args.port)
    if not port:
        return 1
    dest_dir = "%s/%s" % (APPS_ROOT, cat)
    dest = "%s/%s" % (dest_dir, entry["file"])
    try:
        with FlipperCLI(port) as flip:
            log.info("Ensuring %s exists on SD card...", dest_dir)
            flip.mkdir_p(dest_dir)
            flip.storage_write(dest, data)
            # Best-effort on-device integrity check via md5.
            md5_dev = flip.storage_md5(dest)
            md5_local = hashlib.md5(data).hexdigest()
            if md5_dev and md5_dev != md5_local:
                log.error(
                    "On-device md5 (%s) != local md5 (%s). The transfer may "
                    "be corrupt -- consider qFlipper.", md5_dev, md5_local)
                return 1
            if md5_dev:
                log.info("On-device md5 matches local file. Upload verified.")
            else:
                log.warning(
                    "Could not read on-device md5; upload not independently "
                    "verified. If the app misbehaves, re-install via qFlipper.")
    except FlipperError as e:
        log.error("Install failed: %s", e)
        return 1
    print("Installed %s -> %s" % (entry.get("name"), dest))
    return 0


def _channel_entries(catalog, channel):
    """Return the list of firmware entries for a channel, or []."""
    return (catalog.get("channels") or {}).get(channel, [])


def cmd_list_firmware(args):
    """Show firmware bundles from the synced catalog for a channel."""
    catalog = load_cached_json("firmware-directory.json")
    if catalog is None:
        return 1
    channels = [args.channel] if args.channel else list(
        (catalog.get("channels") or {}).keys())
    any_rows = False
    for ch in channels:
        entries = _channel_entries(catalog, ch)
        if not entries:
            continue
        any_rows = True
        print("== channel: %s ==" % ch)
        print("%-16s %-16s %12s  %s" % ("VERSION", "TARGET", "SIZE", "FILE"))
        print("-" * 70)
        for e in entries:
            print("%-16s %-16s %12d  %s" % (
                e.get("version", "?"), e.get("target", "?"),
                e.get("size", 0), e.get("file", "?")))
            if e.get("info"):
                print("    %s" % e["info"])
        print()
    if not any_rows:
        print("No firmware found for channel(s): %s" % ", ".join(channels))
    return 0


def _select_firmware(catalog, channel, version):
    """
    Pick a firmware entry: the requested version, or the first entry in the
    channel (treated as newest, since the server lists newest-first).
    Returns the entry dict or None.
    """
    entries = _channel_entries(catalog, channel)
    if not entries:
        return None
    if version:
        for e in entries:
            if e.get("version") == version:
                return e
        return None
    return entries[0]


# A big, hard-to-miss warning banner for the flashing operation.
FLASH_WARNING = r"""
################################################################################
#                        !!!  FIRMWARE FLASH WARNING  !!!                      #
################################################################################
#  You are about to install firmware on your Flipper Zero.                     #
#                                                                              #
#   * DO NOT disconnect the USB cable during flashing.                         #
#   * DO NOT power off or let the battery die mid-update.                      #
#   * Make sure the battery is reasonably CHARGED before continuing.           #
#   * The device WILL reboot into its updater and may appear unresponsive      #
#     for a minute or two. This is normal.                                     #
#                                                                              #
#  Flashing firmware is inherently risky. If the update is interrupted you     #
#  may need to recover the device with qFlipper. The CLI upload path used      #
#  here is best-effort; qFlipper is the FULLY-SUPPORTED, reliable method for   #
#  firmware installation.                                                      #
################################################################################
"""


def cmd_update_firmware(args):
    """Download/verify/extract a firmware bundle and flash it to the device."""
    catalog = load_cached_json("firmware-directory.json")
    if catalog is None:
        return 1
    entry = _select_firmware(catalog, args.channel, args.version)
    if entry is None:
        log.error("No matching firmware on channel %r%s. Try `list-firmware`.",
                  args.channel,
                  (" for version %r" % args.version) if args.version else "")
        return 1

    server = args.server.rstrip("/")
    rel = entry.get("path") or ("/firmware/%s" % entry["file"])
    url = server + rel
    expected = entry.get("sha256", "").lower()

    log.info("Selected firmware %s (%s) on channel %s",
             entry.get("version"), entry.get("target"), args.channel)
    log.info("Downloading %s", url)
    data = http_get_bytes(url)

    # --- mandatory sha256 verification (refuse to flash on mismatch) ----
    if not expected:
        log.error("Catalog entry has no sha256; refusing to flash blindly.")
        return 1
    actual = sha256_bytes(data)
    if actual != expected:
        log.error("SHA256 MISMATCH for firmware %s!\n  expected %s\n  got      %s",
                  entry.get("file"), expected, actual)
        log.error("Refusing to flash firmware that failed verification.")
        return 1
    log.info("SHA256 verified OK (%s).", actual)

    # --- extract the .tgz into a temp dir -------------------------------
    # The bundle contains update.fuf + binaries that must land under
    # /ext/update/<Name>/ on the SD card.
    with tempfile.TemporaryDirectory(prefix="zxmware-fw-") as tmp:
        tmp_path = Path(tmp)
        archive = tmp_path / entry["file"]
        with open(archive, "wb") as fh:
            fh.write(data)
        extract_root = tmp_path / "unpacked"
        extract_root.mkdir()
        log.info("Extracting %s ...", entry["file"])
        _safe_extract_tgz(archive, extract_root)

        # Locate the directory that actually contains update.fuf.
        fuf_dir = _find_update_dir(extract_root)
        if fuf_dir is None:
            log.error("Could not find update.fuf in the extracted bundle. "
                      "This bundle may be malformed, or you should install it "
                      "with qFlipper instead.")
            return 1
        files = sorted(p for p in fuf_dir.rglob("*") if p.is_file())
        log.info("Bundle contains %d file(s) to push.", len(files))

        # --- loud safety warning + confirmation -------------------------
        print(FLASH_WARNING)
        if not args.yes:
            resp = input(
                "Type 'flash' to proceed with installing firmware %s: "
                % entry.get("version")
            ).strip().lower()
            if resp != "flash":
                print("Aborted. No changes made to the device.")
                return 1
        else:
            log.warning("--yes supplied: skipping interactive confirmation.")

        # --- push the folder, then trigger the install ------------------
        port = resolve_port(args.port)
        if not port:
            return 1
        try:
            with FlipperCLI(port) as flip:
                # Clean/prepare the staging dir on the SD card.
                log.info("Preparing %s on the SD card...", FIRMWARE_UPDATE_DIR)
                flip.mkdir_p(FIRMWARE_UPDATE_DIR)
                for f in files:
                    rel_path = f.relative_to(fuf_dir).as_posix()
                    dest = "%s/%s" % (FIRMWARE_UPDATE_DIR, rel_path)
                    # Create any nested subdirectory the bundle needs.
                    parent = dest.rsplit("/", 1)[0]
                    flip.mkdir_p(parent)
                    with open(f, "rb") as fh:
                        payload = fh.read()
                    flip.storage_write(dest, payload)
                    # Best-effort per-file md5 check.
                    md5_dev = flip.storage_md5(dest)
                    if md5_dev and md5_dev != hashlib.md5(payload).hexdigest():
                        log.error("md5 mismatch pushing %s; aborting before "
                                  "flash. Use qFlipper.", dest)
                        return 1

                fuf_on_device = "%s/update.fuf" % FIRMWARE_UPDATE_DIR
                log.warning("Issuing `update install %s` -- the device will "
                            "now reboot into the updater.", fuf_on_device)
                out = flip.send("update install %s" % fuf_on_device,
                                extra_timeout=20.0)
                log.info("Device response: %s", out or "(device rebooting)")
        except FlipperError as e:
            log.error("Firmware flash failed: %s", e)
            log.error("If the device is now unresponsive, recover it with "
                      "qFlipper.")
            return 1

    print("\nFirmware install triggered. The Flipper is rebooting into its "
          "updater -- leave it connected and powered until it finishes.")
    print("If anything looks wrong, qFlipper is the supported recovery path.")
    return 0


def _safe_extract_tgz(archive, dest):
    """
    Extract a .tgz safely, guarding against path traversal (Zip/Tar Slip).

    Downloaded archives are untrusted, so we reject any member whose resolved
    path would escape `dest`.
    """
    dest = Path(dest).resolve()
    with tarfile.open(archive, "r:gz") as tar:
        for member in tar.getmembers():
            target = (dest / member.name).resolve()
            if not str(target).startswith(str(dest) + os.sep) and target != dest:
                raise FlipperError(
                    "Refusing to extract unsafe path from archive: %s"
                    % member.name
                )
        # `filter='data'` (Python 3.12+) also strips dangerous members; use it
        # when available, else fall back to our validated extractall.
        try:
            tar.extractall(dest, filter="data")  # type: ignore[arg-type]
        except TypeError:
            tar.extractall(dest)


def _find_update_dir(root):
    """Return the directory under `root` that contains update.fuf, or None."""
    root = Path(root)
    for fuf in root.rglob("update.fuf"):
        return fuf.parent
    return None


def cmd_free(args):
    """Show SD card free space via `storage info /ext`."""
    port = resolve_port(args.port)
    if not port:
        return 1
    try:
        with FlipperCLI(port) as flip:
            print(flip.storage_info("/ext"))
    except FlipperError as e:
        log.error("Could not read storage info: %s", e)
        return 1
    return 0


# -----------------------------------------------------------------------------
# Argument parsing / main
# -----------------------------------------------------------------------------

def build_parser():
    p = argparse.ArgumentParser(
        prog="zxmware",
        description="Companion CLI for the self-hosted ZxmWare Flipper Zero "
                    "firmware/app distribution system.",
    )
    p.add_argument("-v", "--verbose", action="store_true",
                   help="enable debug logging")
    p.add_argument("--server", default=DEFAULT_SERVER,
                   help="ZxmWare server base URL (default: $ZXMWARE_SERVER or "
                        "http://localhost:8000)")
    sub = p.add_subparsers(dest="command", required=True)

    sp = sub.add_parser("detect", help="list Flipper ports and show device info")
    sp.add_argument("--port", help="serial port to query (default: autodetect)")
    sp.set_defaults(func=cmd_detect)

    sp = sub.add_parser("sync", help="fetch and cache both directory.json files")
    sp.set_defaults(func=cmd_sync)

    sp = sub.add_parser("list-apps", help="list apps from the synced catalog")
    sp.add_argument("--category", help="filter to a single category")
    sp.set_defaults(func=cmd_list_apps)

    sp = sub.add_parser("install-app",
                        help="download a .fap (verify sha256) and push to device")
    sp.add_argument("target", help="app id or name from the catalog")
    sp.add_argument("--port", help="serial port (default: autodetect)")
    sp.set_defaults(func=cmd_install_app)

    sp = sub.add_parser("list-firmware",
                        help="list firmware bundles from the synced catalog")
    sp.add_argument("--channel", choices=["release", "dev"],
                    help="only show this channel")
    sp.set_defaults(func=cmd_list_firmware)

    sp = sub.add_parser("update-firmware",
                        help="download/verify/extract and flash firmware")
    sp.add_argument("--channel", default="release",
                    choices=["release", "dev"],
                    help="firmware channel (default: release)")
    sp.add_argument("--version", help="specific version (default: newest)")
    sp.add_argument("--port", help="serial port (default: autodetect)")
    sp.add_argument("--yes", action="store_true",
                    help="skip the interactive flash confirmation (dangerous)")
    sp.set_defaults(func=cmd_update_firmware)

    sp = sub.add_parser("free", help="show SD card free space")
    sp.add_argument("--port", help="serial port (default: autodetect)")
    sp.set_defaults(func=cmd_free)

    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)-7s %(message)s",
    )
    try:
        return args.func(args)
    except KeyboardInterrupt:
        log.error("Interrupted.")
        return 130
    except Exception as e:  # noqa: BLE001 - top-level guard, report cleanly
        log.error("%s", e)
        if args.verbose:
            raise
        return 1


if __name__ == "__main__":
    sys.exit(main())
