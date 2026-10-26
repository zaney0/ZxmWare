#!/usr/bin/env python3
"""
png_to_splash.py - turn a 128x64 1-bit PNG into Flipper's boot-splash format.

HONEST SCOPE
------------
The Flipper boot splash is a heatshrink-compressed 1-bit image whose encoded form
is produced by the firmware's OWN assets compiler (the same one `fbt` invokes). The
compressor (heatshrink) and the exact "hdgr"/`.bm` framing are defined IN the
firmware tree, not here. Reimplementing them by hand is exactly the kind of thing
that silently produces a corrupt splash, so this tool does NOT fake it.

Instead it:
  * (optionally, if Pillow is installed) validates that the input PNG is really
    128x64 and 1-bit, so you catch a bad asset before the toolchain does;
  * finds the firmware tree's own image/asset compiler (via --fw-src) and shells
    out to it to do the real encoding;
  * if no firmware tree / compiler is given or found, prints a clear explanation of
    what is required rather than emitting a bogus file.

USAGE
    # With a firmware checkout (does the real conversion via the fw's own tool):
    python3 -I png_to_splash.py --in logo.png --out splash.bin --fw-src /path/to/fw

    # Validate-only (no --fw-src): checks dimensions/bit-depth if Pillow is present.
    python3 -I png_to_splash.py --in logo.png

NOTE: the resulting splash can also be injected at pack time with
      pack_update.py rebrand --splash <splash.bin>, which swaps it into the final
      OTA bundle without rebuilding firmware.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

EXPECTED_W, EXPECTED_H = 128, 64

# Optional dependency - guarded so the script runs on a bare stdlib install.
try:
    from PIL import Image  # type: ignore

    _HAVE_PIL = True
except Exception:  # pragma: no cover - Pillow simply not installed
    _HAVE_PIL = False


def log(msg: str) -> None:
    print(f"[splash] {msg}")


def warn(msg: str) -> None:
    print(f"[splash][!] {msg}", file=sys.stderr)


def validate_png(png: Path) -> bool:
    """Best-effort check that the PNG is 128x64 1-bit. Returns True if it looks OK
    (or if we cannot check because Pillow is missing)."""
    if not png.is_file():
        sys.exit(f"[splash][x] input not found: {png}")

    if not _HAVE_PIL:
        warn("Pillow not installed - skipping dimension/bit-depth validation "
             "(install 'Pillow' to enable it).")
        return True

    with Image.open(png) as im:
        w, h, mode = im.width, im.height, im.mode
    log(f"input: {png.name}  {w}x{h}  mode={mode}")

    ok = True
    if (w, h) != (EXPECTED_W, EXPECTED_H):
        warn(f"expected {EXPECTED_W}x{EXPECTED_H}, got {w}x{h} - Flipper splash must be 128x64")
        ok = False
    # Flipper splash is 1-bit. Mode "1" is ideal; "L"/"P"/"RGB" will need flattening
    # by the firmware tool but a non-1-bit image is worth flagging.
    if mode != "1":
        warn(f"image mode is {mode!r}, not 1-bit ('1') - make it pure black/white to be safe")
    return ok


# Known entry points for the firmware's asset/image compiler, newest layout first.
# We try each that exists under --fw-src and look for an image-convert subcommand.
def find_fw_tool(fw_src: Path) -> list[str] | None:
    """Return an argv prefix for the firmware's asset compiler, or None."""
    candidates = [
        fw_src / "scripts" / "assets.py",
        fw_src / "scripts" / "flipper" / "assets.py",
        fw_src / "scripts" / "icons.py",
    ]
    for tool in candidates:
        if tool.is_file():
            log(f"found firmware asset tool: {tool}")
            return [sys.executable, "-I", str(tool)]
    return None


def convert_with_fw(fw_src: Path, png: Path, out: Path) -> int:
    """Shell out to the firmware tree's own compiler to produce the splash.

    Different forks expose slightly different subcommands; we try the common ones
    ('icon' / 'image') and report clearly if none is accepted rather than guessing
    at the binary format ourselves.
    """
    prefix = find_fw_tool(fw_src)
    if prefix is None:
        warn(
            "no asset compiler found under "
            f"{fw_src}/scripts/. The firmware tree's assets compiler is REQUIRED to "
            "encode the 'hdgr'/heatshrink splash - this tool will not hand-roll that "
            "format. Point --fw-src at a full firmware checkout, or let `fbt` build the "
            "splash from the PNG placed in assets/ by apply_branding.py."
        )
        return 2

    out.parent.mkdir(parents=True, exist_ok=True)
    # Try a couple of likely invocations; the firmware tool validates the real format.
    attempts = [
        prefix + ["icon", str(png), str(out)],
        prefix + ["image", str(png), str(out)],
        prefix + ["--help"],  # last resort: show the tool's own usage to the user
    ]
    for argv in attempts:
        log("running: " + " ".join(argv))
        try:
            res = subprocess.run(argv, cwd=str(fw_src), capture_output=True, text=True)
        except OSError as e:
            warn(f"could not execute asset tool: {e}")
            return 2
        if res.returncode == 0 and argv[-1] != "--help":
            log(f"wrote {out} ({out.stat().st_size if out.is_file() else '?'} bytes)")
            return 0
        if argv[-1] == "--help":
            # We exhausted the real attempts; surface the tool's usage so the user
            # can run the correct subcommand for their fork.
            warn("could not auto-detect the right subcommand for this fork.")
            if res.stdout:
                print(res.stdout)
            if res.stderr:
                print(res.stderr, file=sys.stderr)
            warn("re-run the firmware asset tool manually with the subcommand it lists.")
            return 3
        # non-help attempt failed; note it and try the next form
        warn(f"attempt failed (rc={res.returncode}): {res.stderr.strip() or res.stdout.strip()}")
    return 3


def main() -> int:
    ap = argparse.ArgumentParser(description="Convert a 128x64 1-bit PNG to a Flipper splash.")
    ap.add_argument("--in", dest="inp", required=True, help="source PNG (128x64, 1-bit)")
    ap.add_argument("--out", help="output splash file (required with --fw-src)")
    ap.add_argument("--fw-src", help="firmware checkout; its asset compiler does the real encode")
    args = ap.parse_args()

    png = Path(args.inp).resolve()
    validate_png(png)

    if not args.fw_src:
        log(
            "validate-only mode (no --fw-src). To produce an actual splash.bin you must "
            "supply --fw-src pointing at a firmware checkout, whose assets compiler "
            "encodes the heatshrink/'hdgr' format. This tool intentionally does not "
            "implement that encoder standalone."
        )
        return 0

    if not args.out:
        sys.exit("[splash][x] --out is required when --fw-src is given")

    fw_src = Path(args.fw_src).resolve()
    if not fw_src.is_dir():
        sys.exit(f"[splash][x] --fw-src is not a directory: {fw_src}")

    return convert_with_fw(fw_src, png, Path(args.out).resolve())


if __name__ == "__main__":
    raise SystemExit(main())
