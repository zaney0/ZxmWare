#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
index_repo.py -- ZxmWare repository indexer.

Scans the ZxmWare data tree and writes two JSON "directory" manifests that the
server (and on-device updaters / app catalog clients) consume:

  * server/data/firmware/directory.json  -- firmware bundles, grouped into
    "release" / "dev" channels.
  * server/data/apps/directory.json       -- individual .fap apps, grouped by
    category.

The tool is stdlib-only (designed to be run with `python3 -I`), idempotent, and
never extracts bundles to disk: it peeks inside each firmware .tgz in memory to
read `update.fuf` for the build Info / hardware Target.

Layout it expects (relative to --data-dir):

    firmware/
        ZxmWare-0053.tgz
        ZxmWare-0053.tgz.sha256         (optional sibling, "<hex>  <name>")
        ZxmWare-0054-dev.tgz
    apps/
        NFC/
            mf_classic_dict.fap
        Sub-GHz/
            subbrute.fap

Usage examples:

    python3 -I index_repo.py                      # scan ../data, write manifests
    python3 -I index_repo.py --pretty             # human-readable JSON
    python3 -I index_repo.py --base-url https://ota.example.com
    python3 -I index_repo.py --data-dir /srv/zxmware/data
    python3 -I index_repo.py --dump-resource-manifest firmware/ZxmWare-0053.tgz
"""

import argparse
import hashlib
import json
import os
import sys
import tarfile
import time

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Repository display names baked into each manifest.
FIRMWARE_REPO_NAME = "ZxmWare"
APPS_REPO_NAME = "ZxmWare Apps"

# Filename tag that forces a bundle onto the "dev" channel; everything else is
# treated as a "release".
DEV_TAG = "-dev"

# Known Flipper app categories. We don't *enforce* this list (any directory
# under apps/ becomes a category) -- it's here for documentation and so the
# summary can hint at unexpected directories.
KNOWN_CATEGORIES = (
    "Bluetooth", "GPIO", "Games", "Infrared", "Media", "NFC", "RFID",
    "Sub-GHz", "Tools", "USB", "WiFi", "iButton",
)

# Read files in reasonably large chunks when hashing.
_HASH_CHUNK = 1024 * 1024


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------

def sha256_file(path):
    """Return the lowercase hex sha256 of a file on disk."""
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(_HASH_CHUNK), b""):
            h.update(chunk)
    return h.hexdigest()


def sha256_stream(fileobj):
    """Return the lowercase hex sha256 of a readable binary stream."""
    h = hashlib.sha256()
    for chunk in iter(lambda: fileobj.read(_HASH_CHUNK), b""):
        h.update(chunk)
    return h.hexdigest()


def read_sibling_sha256(tgz_path):
    """
    Read a sibling `<file>.sha256` if present.

    The sidecar format is the standard coreutils one: "<hex>  <filename>".
    We only trust the hex field and ignore the (possibly mismatched) filename.
    Returns the hex string, or None if there is no usable sidecar.
    """
    sidecar = tgz_path + ".sha256"
    if not os.path.isfile(sidecar):
        return None
    try:
        with open(sidecar, "r", encoding="utf-8", errors="replace") as fh:
            first = fh.readline().strip()
    except OSError:
        return None
    if not first:
        return None
    # Split on whitespace; the hex digest is the first token.
    token = first.split()[0]
    token = token.lower().lstrip("*")  # some tools prefix the name with '*'
    # Basic sanity check: a sha256 is 64 hex chars.
    if len(token) == 64 and all(c in "0123456789abcdef" for c in token):
        return token
    return None


def parse_fuf(text):
    """
    Parse `update.fuf`-style key:value text into a dict.

    Lines look like `Info: ZxmWare-0053` or `Target: 7`. Blank lines and lines
    without a colon are ignored. Keys are kept verbatim (first colon splits).
    """
    result = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or ":" not in line:
            continue
        key, _, value = line.partition(":")
        result[key.strip()] = value.strip()
    return result


def version_from_filename(name):
    """
    Derive a version string from a bundle filename.

    `ZxmWare-0053.tgz`      -> "0053"
    `ZxmWare-0054-dev.tgz`  -> "0054-dev"
    Falls back to the stem if the expected `<Name>-<ver>` shape is absent.
    """
    stem = name
    if stem.endswith(".tgz"):
        stem = stem[: -len(".tgz")]
    elif stem.endswith(".tar.gz"):
        stem = stem[: -len(".tar.gz")]
    # Split once on the first '-': left is the build name, right is the version.
    if "-" in stem:
        _, _, ver = stem.partition("-")
        return ver
    return stem


def channel_for(name):
    """Return 'dev' if the filename carries the dev tag, else 'release'."""
    return "dev" if DEV_TAG in name else "release"


# ---------------------------------------------------------------------------
# Firmware bundle inspection (no extraction to disk)
# ---------------------------------------------------------------------------

def peek_update_fuf(tgz_path):
    """
    Open a firmware .tgz and read the single top-level `<Name>-<ver>/update.fuf`
    entirely in memory (via tarfile.extractfile), returning the parsed dict.

    Returns {} if the bundle has no update.fuf or cannot be read as a tar.
    """
    try:
        with tarfile.open(tgz_path, mode="r:*") as tf:
            member = _find_member(tf, basename="update.fuf")
            if member is None:
                return {}
            extracted = tf.extractfile(member)
            if extracted is None:
                return {}
            with extracted:
                data = extracted.read()
            return parse_fuf(data.decode("utf-8", errors="replace"))
    except (tarfile.TarError, OSError):
        return {}


def _find_member(tf, basename):
    """
    Find the first tar member whose path basename matches `basename`.

    We don't assume the exact top-dir name; we just look for `.../update.fuf`
    one level under the single top directory.
    """
    for member in tf.getmembers():
        if not member.isfile():
            continue
        if os.path.basename(member.name) == basename:
            return member
    return None


# ---------------------------------------------------------------------------
# resources.tar Manifest parsing (V / T / D / F lines)
# ---------------------------------------------------------------------------

def parse_resource_manifest(text):
    """
    Parse an on-device SD resource `Manifest` into structured data.

    Recognized line kinds:
        V:<int>                     -- manifest format version
        T:<unixtime>                -- build timestamp
        D:<dir>                     -- a directory entry
        F:<md5>:<size>:<relpath>    -- a file entry

    Returns a dict:
        {
          "version": <int or None>,
          "timestamp": <int or None>,
          "dirs": ["apps", "apps/NFC", ...],
          "files": [{"md5": "...", "size": 123, "path": "apps/NFC/x.fap"}, ...],
        }

    Unknown line kinds are ignored so the parser tolerates future additions.
    """
    out = {"version": None, "timestamp": None, "dirs": [], "files": []}
    for raw in text.splitlines():
        line = raw.rstrip("\n")
        if not line:
            continue
        kind, _, rest = line.partition(":")
        if kind == "V":
            out["version"] = _safe_int(rest)
        elif kind == "T":
            out["timestamp"] = _safe_int(rest)
        elif kind == "D":
            out["dirs"].append(rest)
        elif kind == "F":
            # F:<md5>:<size>:<relpath> -- relpath itself may contain ':'? Flipper
            # paths don't, but split carefully: md5 and size are fixed, the rest
            # (including any stray colons) is the path.
            parts = rest.split(":", 2)
            if len(parts) == 3:
                md5, size, path = parts
                out["files"].append({
                    "md5": md5,
                    "size": _safe_int(size),
                    "path": path,
                })
        # else: unknown kind -> ignore
    return out


def _safe_int(s):
    """Parse an int, returning None on failure."""
    try:
        return int(s.strip())
    except (ValueError, AttributeError):
        return None


def extract_resource_manifest_from_tgz(tgz_path):
    """
    Locate resources.tar inside a firmware .tgz, then locate the `Manifest`
    inside that nested tar, and return the parsed manifest structure.

    Everything is done in memory via extractfile; nothing is written to disk.
    Returns the parsed dict, or None if no Manifest could be found.
    """
    try:
        with tarfile.open(tgz_path, mode="r:*") as outer:
            res_member = _find_member(outer, basename="resources.tar")
            if res_member is None:
                return None
            res_stream = outer.extractfile(res_member)
            if res_stream is None:
                return None
            # resources.tar is an uncompressed tar embedded in the gz bundle.
            with res_stream:
                with tarfile.open(fileobj=res_stream, mode="r:*") as inner:
                    man_member = _find_member(inner, basename="Manifest")
                    if man_member is None:
                        return None
                    man_stream = inner.extractfile(man_member)
                    if man_stream is None:
                        return None
                    with man_stream:
                        data = man_stream.read()
            return parse_resource_manifest(data.decode("utf-8", errors="replace"))
    except (tarfile.TarError, OSError):
        return None


# ---------------------------------------------------------------------------
# Scanning
# ---------------------------------------------------------------------------

def _join_url(base_url, path):
    """
    Join an optional base URL with an absolute-style path ("/firmware/...").

    If base_url is empty, the path is returned unchanged. Otherwise we strip a
    trailing slash from base_url and keep the leading slash on path.
    """
    if not base_url:
        return path
    return base_url.rstrip("/") + path


def scan_firmware(firmware_dir, base_url=""):
    """
    Scan firmware_dir for `*.tgz` bundles and build per-channel entry lists.

    Each entry:
        {
          "version": "0053",
          "file": "ZxmWare-0053.tgz",
          "path": "/firmware/ZxmWare-0053.tgz",
          "size": 1234567,
          "sha256": "<hex>",
          "target": "7"  (or None),
          "info": "ZxmWare-0053"  (or None),
        }

    sha256 is read from the sibling `.sha256` when present, else computed.
    """
    channels = {"release": [], "dev": []}
    if not os.path.isdir(firmware_dir):
        return channels

    for name in sorted(os.listdir(firmware_dir)):
        if not name.endswith(".tgz"):
            continue
        tgz_path = os.path.join(firmware_dir, name)
        if not os.path.isfile(tgz_path):
            continue

        digest = read_sibling_sha256(tgz_path)
        if digest is None:
            digest = sha256_file(tgz_path)

        fuf = peek_update_fuf(tgz_path)

        entry = {
            "version": version_from_filename(name),
            "file": name,
            "path": _join_url(base_url, "/firmware/" + name),
            "size": os.path.getsize(tgz_path),
            "sha256": digest,
            "target": fuf.get("Target"),
            "info": fuf.get("Info"),
        }
        channels[channel_for(name)].append(entry)

    return channels


def app_id_from_filename(name):
    """Derive an app id from a `.fap` filename: `subbrute.fap` -> `subbrute`."""
    if name.endswith(".fap"):
        return name[: -len(".fap")]
    return name


def app_display_name(app_id):
    """
    Turn an id into a friendlier display name.

    `mf_classic_dict` -> "Mf Classic Dict". This is a best-effort cosmetic
    transform only -- the id remains the canonical key.
    """
    return " ".join(part.capitalize() for part in app_id.replace("-", "_").split("_") if part)


def scan_apps(apps_dir, base_url=""):
    """
    Scan apps_dir/<Category>/*.fap and build a per-category entry map.

    Each entry:
        {
          "id": "subbrute",
          "name": "Subbrute",
          "category": "Sub-GHz",
          "file": "subbrute.fap",
          "path": "/apps/Sub-GHz/subbrute.fap",
          "size": 12345,
          "sha256": "<hex>",
        }
    """
    categories = {}
    if not os.path.isdir(apps_dir):
        return categories

    for category in sorted(os.listdir(apps_dir)):
        cat_dir = os.path.join(apps_dir, category)
        if not os.path.isdir(cat_dir):
            continue  # skip stray files like directory.json itself

        entries = []
        for name in sorted(os.listdir(cat_dir)):
            if not name.endswith(".fap"):
                continue
            fap_path = os.path.join(cat_dir, name)
            if not os.path.isfile(fap_path):
                continue
            app_id = app_id_from_filename(name)
            entries.append({
                "id": app_id,
                "name": app_display_name(app_id),
                "category": category,
                "file": name,
                "path": _join_url(base_url, "/apps/" + category + "/" + name),
                "size": os.path.getsize(fap_path),
                "sha256": sha256_file(fap_path),
            })

        if entries:
            categories[category] = entries

    return categories


# ---------------------------------------------------------------------------
# Manifest writing
# ---------------------------------------------------------------------------

def write_json(path, obj, pretty):
    """Write obj to path as JSON. Pretty adds indentation + sorted keys."""
    if pretty:
        text = json.dumps(obj, indent=2, sort_keys=True, ensure_ascii=False)
    else:
        text = json.dumps(obj, separators=(",", ":"), ensure_ascii=False)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)
        fh.write("\n")


def build_firmware_manifest(channels, now):
    return {
        "name": FIRMWARE_REPO_NAME,
        "generated": now,
        "channels": channels,
    }


def build_apps_manifest(categories, now):
    return {
        "name": APPS_REPO_NAME,
        "generated": now,
        "categories": categories,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def default_data_dir():
    """Resolve ../data relative to this script, as an absolute path."""
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.abspath(os.path.join(here, "..", "data"))


def build_arg_parser():
    p = argparse.ArgumentParser(
        prog="index_repo.py",
        description="Index the ZxmWare data tree into firmware/apps directory.json manifests.",
    )
    p.add_argument(
        "--data-dir",
        default=default_data_dir(),
        help="Path to the server data directory (default: ../data relative to this script).",
    )
    p.add_argument(
        "--base-url",
        default="",
        help="Optional URL prefix for entry paths, e.g. https://ota.example.com.",
    )
    p.add_argument(
        "--pretty",
        action="store_true",
        help="Write human-readable, indented JSON.",
    )
    p.add_argument(
        "--dump-resource-manifest",
        metavar="TGZ",
        default=None,
        help="Debug: parse the resources.tar Manifest inside TGZ and print it as JSON, then exit. "
             "TGZ may be absolute or relative to --data-dir.",
    )
    return p


def run_dump_resource_manifest(data_dir, tgz_arg, pretty):
    """Handle the --dump-resource-manifest debug path. Returns an exit code."""
    tgz_path = tgz_arg
    if not os.path.isabs(tgz_path):
        tgz_path = os.path.join(data_dir, tgz_arg)
    tgz_path = os.path.abspath(tgz_path)

    if not os.path.isfile(tgz_path):
        print("error: no such bundle: %s" % tgz_path, file=sys.stderr)
        return 2

    manifest = extract_resource_manifest_from_tgz(tgz_path)
    if manifest is None:
        print("error: no resources.tar/Manifest found in %s" % tgz_path, file=sys.stderr)
        return 1

    if pretty:
        print(json.dumps(manifest, indent=2, sort_keys=True, ensure_ascii=False))
    else:
        print(json.dumps(manifest, separators=(",", ":"), ensure_ascii=False))
    return 0


def main(argv=None):
    args = build_arg_parser().parse_args(argv)

    data_dir = os.path.abspath(args.data_dir)

    # Debug path: dump a resource manifest and exit without touching anything.
    if args.dump_resource_manifest is not None:
        return run_dump_resource_manifest(data_dir, args.dump_resource_manifest, args.pretty)

    if not os.path.isdir(data_dir):
        print("error: data dir does not exist: %s" % data_dir, file=sys.stderr)
        return 2

    firmware_dir = os.path.join(data_dir, "firmware")
    apps_dir = os.path.join(data_dir, "apps")

    now = int(time.time())

    # --- Firmware -------------------------------------------------------
    channels = scan_firmware(firmware_dir, base_url=args.base_url)
    firmware_manifest = build_firmware_manifest(channels, now)
    firmware_out = os.path.join(firmware_dir, "directory.json")
    # Only write if the firmware dir actually exists; otherwise skip silently.
    if os.path.isdir(firmware_dir):
        write_json(firmware_out, firmware_manifest, args.pretty)

    # --- Apps -----------------------------------------------------------
    categories = scan_apps(apps_dir, base_url=args.base_url)
    apps_manifest = build_apps_manifest(categories, now)
    apps_out = os.path.join(apps_dir, "directory.json")
    if os.path.isdir(apps_dir):
        write_json(apps_out, apps_manifest, args.pretty)

    # --- Summary --------------------------------------------------------
    _print_summary(channels, categories, firmware_out, apps_out,
                   firmware_exists=os.path.isdir(firmware_dir),
                   apps_exists=os.path.isdir(apps_dir))
    return 0


def _print_summary(channels, categories, firmware_out, apps_out,
                   firmware_exists, apps_exists):
    print("ZxmWare indexer summary")
    print("=======================")

    if firmware_exists:
        rel = channels.get("release", [])
        dev = channels.get("dev", [])
        print("Firmware -> %s" % firmware_out)
        print("  release: %d bundle(s)" % len(rel))
        for e in rel:
            print("    - %s (target=%s)" % (e["version"], e["target"]))
        print("  dev:     %d bundle(s)" % len(dev))
        for e in dev:
            print("    - %s (target=%s)" % (e["version"], e["target"]))
    else:
        print("Firmware -> (no firmware/ dir, skipped)")

    if apps_exists:
        total = sum(len(v) for v in categories.values())
        print("Apps -> %s" % apps_out)
        print("  %d app(s) across %d categor(y/ies)" % (total, len(categories)))
        for cat in sorted(categories):
            count = len(categories[cat])
            flag = "" if cat in KNOWN_CATEGORIES else "  (unknown category)"
            print("    - %-10s %d%s" % (cat, count, flag))
    else:
        print("Apps -> (no apps/ dir, skipped)")


if __name__ == "__main__":
    sys.exit(main())
