#!/usr/bin/env python3
"""
apply_branding.py - apply ZxmWare branding to a Flipper firmware source tree.

What it does (and ONLY this):
  1. Sets the firmware name / version suffix through fbt's own mechanism:
       - DIST_SUFFIX   -> appended to the name of the produced bundle/dist folder
       - FIRMWARE_ORIGIN -> the firmware "origin"/brand string reported by the build
     These are written to `fbt.user` at the source root (a gitignored file that fbt
     sources for per-user build options), and, if present, mirrored into
     `fbt_options.py` so a plain `./fbt` run picks them up too.
  2. Copies a custom boot splash asset into the firmware's assets path, if that
     path exists in the tree.

What it NEVER does:
  * It never touches Target, CRC, or any OB-* (option-byte) values. Those live in
    the generated update.fuf and in linker/target configs; changing them bricks the
    device. This script only sets cosmetic name/version/splash.

It is deliberately defensive: it only edits files/paths that already exist (except
fbt.user, which it is expected to create), prints every change it makes, and makes a
.zxwbak backup before rewriting any existing file.

stdlib only. Run isolated:
    python3 -I apply_branding.py --fw-src /path/to/fw --name ZxmWare --version 0053 \
        --splash branding/zxmware_splash.bin
"""
from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path


def log(msg: str) -> None:
    print(f"[branding] {msg}")


def warn(msg: str) -> None:
    print(f"[branding][!] {msg}", file=sys.stderr)


def backup_once(path: Path) -> None:
    """Make a one-time .zxwbak backup of an existing file before we rewrite it."""
    bak = path.with_suffix(path.suffix + ".zxwbak")
    if path.is_file() and not bak.exists():
        shutil.copy2(path, bak)
        log(f"backed up {path.name} -> {bak.name}")


def write_fbt_user(fw_src: Path, name: str, version: str) -> None:
    """
    Create/refresh fbt.user with the ZxmWare DIST_SUFFIX / FIRMWARE_ORIGIN.

    fbt.user is a shell file that fbt sources for per-user options. We manage only
    a clearly-delimited ZxmWare block so any existing user settings are preserved.
    """
    fbt_user = fw_src / "fbt.user"
    begin = "# >>> ZxmWare branding >>>"
    end = "# <<< ZxmWare branding <<<"
    block = "\n".join(
        [
            begin,
            f'export DIST_SUFFIX="{version}"',
            f'export FIRMWARE_ORIGIN="{name}"',
            # DIST_SUFFIX also drives the dist/ folder + .tgz naming for fbt.
            end,
            "",
        ]
    )

    existing = fbt_user.read_text() if fbt_user.is_file() else ""
    if begin in existing and end in existing:
        head = existing.split(begin, 1)[0].rstrip("\n")
        tail = existing.split(end, 1)[1].lstrip("\n")
        new = (head + "\n\n" if head else "") + block + (tail if tail else "")
    else:
        new = (existing.rstrip("\n") + "\n\n" if existing.strip() else "") + block

    if fbt_user.is_file():
        backup_once(fbt_user)
    fbt_user.write_text(new)
    log(f"wrote {fbt_user} (DIST_SUFFIX={version}, FIRMWARE_ORIGIN={name})")


def patch_fbt_options(fw_src: Path, name: str, version: str) -> None:
    """
    If fbt_options.py exists, append/refresh a ZxmWare override block so that a bare
    `./fbt` (which doesn't read env from fbt.user in every fork) still gets branded.

    We only APPEND a delimited block; we do not parse or mutate existing assignments.
    """
    opts = fw_src / "fbt_options.py"
    if not opts.is_file():
        log("no fbt_options.py found - skipping (fbt.user covers the build)")
        return

    begin = "# >>> ZxmWare branding >>>"
    end = "# <<< ZxmWare branding <<<"
    block = "\n".join(
        [
            begin,
            "# Overrides appended by apply_branding.py - cosmetic name/version only.",
            f'DIST_SUFFIX = "{version}"',
            f'FIRMWARE_ORIGIN = "{name}"',
            end,
            "",
        ]
    )
    text = opts.read_text()
    backup_once(opts)
    if begin in text and end in text:
        head = text.split(begin, 1)[0].rstrip("\n")
        tail = text.split(end, 1)[1].lstrip("\n")
        new = (head + "\n\n" if head else "") + block + (tail if tail else "")
    else:
        new = text.rstrip("\n") + "\n\n" + block
    opts.write_text(new)
    log(f"patched {opts.name} with DIST_SUFFIX / FIRMWARE_ORIGIN")


# Candidate boot-splash locations across the common forks. We copy to each that
# already exists; we never create new asset directories (that could confuse the
# assets compiler). The on-device boot splash is built from one of these.
SPLASH_CANDIDATES = (
    "assets/slideshow/boot/0.bm",          # some forks keep a boot slideshow frame
    "assets/dolphin/custom/splash.bm",     # custom-dolphin splash
    "assets/icons/Logo/splash.bm",         # logo asset
    "assets/splash.bin",                   # already-compiled splash drop-in
)


def copy_splash(fw_src: Path, splash: Path) -> None:
    """Copy the provided splash into whichever known assets path(s) already exist."""
    if not splash.is_file():
        warn(f"splash not found: {splash} - skipping splash branding")
        return

    copied = 0
    for rel in SPLASH_CANDIDATES:
        dst = fw_src / rel
        if dst.parent.is_dir():  # only write where the assets dir already exists
            backup_once(dst)
            shutil.copy2(splash, dst)
            log(f"copied splash -> {dst}")
            copied += 1

    if copied == 0:
        warn(
            "no known assets splash path exists in this tree; leaving splash to the "
            "rebrand step (pack_update.py --splash swaps splash.bin in the final bundle)."
        )


def main() -> int:
    ap = argparse.ArgumentParser(description="Apply ZxmWare branding to a firmware source tree.")
    ap.add_argument("--fw-src", required=True, help="firmware source checkout root")
    ap.add_argument("--name", default="ZxmWare", help="brand / FIRMWARE_ORIGIN (default: ZxmWare)")
    ap.add_argument("--version", required=True, help="version suffix / DIST_SUFFIX, e.g. 0053")
    ap.add_argument("--splash", help="optional splash file (.bin/.bm) to copy into assets")
    args = ap.parse_args()

    fw_src = Path(args.fw_src).resolve()
    if not fw_src.is_dir():
        sys.exit(f"[branding][x] --fw-src is not a directory: {fw_src}")
    if not (fw_src / "fbt").exists():
        warn(f"{fw_src} has no `fbt` - this may not be a Flipper firmware checkout")

    log(f"source tree: {fw_src}")
    write_fbt_user(fw_src, args.name, args.version)
    patch_fbt_options(fw_src, args.name, args.version)
    if args.splash:
        copy_splash(fw_src, Path(args.splash).resolve())

    log("done - Target/CRC/OB values were NOT touched.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
