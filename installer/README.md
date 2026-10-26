# ZxmWare Companion CLI

`zxmware.py` is the companion command-line installer/updater for the
self-hosted **ZxmWare** Flipper Zero firmware/app distribution system. It talks
to:

- your **ZxmWare server** over HTTP (to fetch the app/firmware catalogs and
  download `.fap` apps and firmware `.tgz` bundles), and
- your **Flipper Zero** over its USB serial CLI (to push files to the SD card
  and trigger firmware installs).

---

## Install

Requires Python 3.8+.

```bash
cd installer
python3 -m pip install -r requirements.txt
```

`requirements.txt` pulls in:

- **pyserial** – required for anything that touches the device.
- **requests** – used for HTTP if present; the tool falls back to the stdlib
  `urllib` if it is not installed, so catalog/network commands still work
  without it.

You can run the tool directly:

```bash
python3 zxmware.py --help
# or make it executable:
chmod +x zxmware.py
./zxmware.py --help
```

---

## Configure the server

The default server is `http://localhost:8000`. Override it with the
`ZXMWARE_SERVER` environment variable or the `--server` flag (the flag wins):

```bash
export ZXMWARE_SERVER="http://192.168.1.50:8000"
# or per-command:
python3 zxmware.py --server http://192.168.1.50:8000 sync
```

Catalogs are cached locally under `~/.zxmware/cache/`.

---

## Finding your Flipper's serial port

Most commands auto-detect the port by USB VID/PID (`0x0483/0x5740`) and name.
If auto-detection fails, pass `--port`:

- **macOS:** `/dev/tty.usbmodemflip_XXXXXX`
- **Linux:** `/dev/ttyACM0` (you may need to be in the `dialout` group)
- **Windows:** `COM3` (or similar)

Make sure the Flipper is unlocked and not currently in use by another program
(for example qFlipper), since the serial port can only be held by one process.

---

## Commands

### `detect` – list ports and show device info

```bash
python3 zxmware.py detect
python3 zxmware.py detect --port /dev/tty.usbmodemflip_Abc123
```

Lists candidate serial ports and prints the device info reported by the
`device_info` (or legacy `info`) CLI command.

### `sync` – fetch and cache the catalogs

```bash
python3 zxmware.py sync
python3 zxmware.py --server http://192.168.1.50:8000 sync
```

Downloads `/firmware/directory.json` and `/apps/directory.json`, validates
them, and caches them in `~/.zxmware/cache/`. Run this before any
`list-*`/`install-*`/`update-*` command.

### `list-apps` – browse the app catalog

```bash
python3 zxmware.py list-apps
python3 zxmware.py list-apps --category GPIO
```

### `install-app` – download and install a `.fap`

```bash
python3 zxmware.py install-app marauder
python3 zxmware.py install-app "WiFi Marauder" --port /dev/ttyACM0
```

Accepts an app **id** or **name** from the catalog. The tool:

1. downloads the `.fap` from the server,
2. **verifies its SHA-256** against the catalog (install is refused on
   mismatch or if the catalog has no hash),
3. creates `/ext/apps/<Category>/` on the SD card if needed, and
4. writes the file, then does a best-effort on-device MD5 check.

### `list-firmware` – browse firmware bundles

```bash
python3 zxmware.py list-firmware
python3 zxmware.py list-firmware --channel dev
```

### `update-firmware` – download, verify, and flash firmware

```bash
# Newest release build, interactive confirmation:
python3 zxmware.py update-firmware

# A specific dev build on a specific port:
python3 zxmware.py update-firmware --channel dev --version 0.103.1-zxm --port /dev/ttyACM0

# Non-interactive (skips the confirmation prompt — be careful):
python3 zxmware.py update-firmware --yes
```

The tool downloads the `.tgz`, **verifies its SHA-256 (flashing is refused on
mismatch)**, extracts it, pushes the `update.fuf` + binaries to
`/ext/update/ZxmWare/` on the SD card, prints a loud safety warning, and —
**only after you type `flash` to confirm** (unless `--yes`) — issues
`update install /ext/update/ZxmWare/update.fuf`, which reboots the device into
its updater.

### `free` – show SD card free space

```bash
python3 zxmware.py free
```

Prints the raw output of `storage info /ext`.

### Global flags

- `-v` / `--verbose` – debug logging (serial traffic, HTTP URLs, chunk
  progress).
- `--server URL` – override the server base URL.

---

## Safety warnings — please read

Flashing firmware is **inherently risky**. A bad or interrupted flash can leave
your Flipper Zero temporarily unbootable.

- **Do not disconnect the USB cable during flashing.**
- **Do not power off** or let the battery die mid-update.
- **Charge the battery** before updating.
- The device **reboots into its updater** and may look unresponsive for a
  minute or two — that is normal.
- `update-firmware` always prints a warning banner and requires you to type
  `flash` to proceed (the `--yes` flag bypasses this prompt, so use it only in
  automation you trust).
- SHA-256 verification is **mandatory**. The tool refuses to install any app or
  firmware whose downloaded bytes do not match the catalog hash.

---

## About the serial upload limitation (honest caveat)

Uploading files to the Flipper over its **text CLI** is finicky. The classic
`storage write <path>` command reads bytes from the serial stream until it sees
a `Ctrl-C` (byte `0x03`) — which means a binary payload containing a `0x03`
byte can be truncated. `zxmware.py` prefers the newer, binary-safe
`storage write_chunk <path> <size>` command (which reads an exact byte count,
no terminator needed) and only falls back to the classic path on older
firmware, where it warns about the risk and relies on post-transfer MD5
verification.

Even with those safeguards, CLI transfers are **slow and can be unreliable**
for large files, and depend on your firmware version and host USB stack.

**For large transfers, and for firmware installs in particular, the
fully-supported and reliable path is [qFlipper](https://flipperzero.one/update)
(the official GUI).** Other good options are
[`ufbt`](https://github.com/flipperdevices/flipperzero-ufbt) and
[`pyflipper`](https://github.com/wh00hw/pyFlipper). This CLI implements device
upload on a **best-effort** basis; if a push fails verification or the device
misbehaves afterward, reach for qFlipper.
