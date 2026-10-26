#!/usr/bin/env python3
"""
ZxmWare update-bundle packer / rebrander.

A Flipper OTA bundle is a directory containing:
    update.fuf      - upgrade manifest (Filetype: Flipper firmware upgrade configuration)
    firmware.dfu    - main MCU firmware (DfuSe)
    radio.bin       - BLE copro stack
    updater.bin     - self-updater loader
    resources.tar   - SD-card resources (apps/, dolphin/, ... + Manifest)
    splash.bin      - boot splash (Flipper 'hdgr' compressed image)

This tool can:
  * rebrand an EXISTING bundle (e.g. an Xtreme/Unleashed/official release) into a
    ZxmWare-named bundle - rewrites update.fuf `Info:` and swaps the splash - then
    repackages it as `ZxmWare-<version>.tgz` (the format the Flipper updater installs).
  * pack a freshly built firmware tree's `dist/.../` output the same way.

It never touches Target/CRC/OB values (doing so bricks devices); it only renames and
reskins. Run with -I so planted modules in the bundle dir can't be imported:

    python3 -I pack_update.py rebrand \\
        --src   /path/to/XFW-0053_02022024 \\
        --out   ../server/data/firmware \\
        --name  ZxmWare --version 0053 \\
        --splash branding/zxmware_splash.bin
"""
from __future__ import annotations
import argparse, hashlib, os, re, shutil, sys, tarfile, tempfile, time
from pathlib import Path

REQUIRED = ["update.fuf"]  # everything else is referenced BY update.fuf


def sha256(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def read_fuf(path: Path) -> list[tuple[str, str]]:
    """Parse update.fuf preserving order and comments (comments stored as (None, line))."""
    out = []
    for line in path.read_text().splitlines():
        if line.startswith("#") or not line.strip():
            out.append((None, line))
            continue
        if ":" in line:
            k, v = line.split(":", 1)
            out.append((k.strip(), v.strip()))
        else:
            out.append((None, line))
    return out


def write_fuf(entries: list[tuple[str, str]], path: Path) -> None:
    lines = []
    for k, v in entries:
        lines.append(v if k is None else f"{k}: {v}")
    path.write_text("\n".join(lines) + "\n")


def rebrand(args) -> int:
    src = Path(args.src).resolve()
    fuf = src / "update.fuf"
    if not fuf.is_file():
        sys.exit(f"[!] no update.fuf in {src} - is this a Flipper OTA bundle?")

    entries = read_fuf(fuf)
    keys = {k for k, _ in entries if k}
    print(f"[*] source bundle: {src.name}  (manifest keys: {sorted(keys)})")

    stage = Path(tempfile.mkdtemp(prefix="zxmware_pack_"))
    try:
        # copy every file the manifest references, plus the manifest itself
        referenced = {v for k, v in entries if k in (
            "Loader", "Firmware", "Radio", "Resources", "Splashscreen")}
        for name in referenced:
            s = src / name
            if not s.is_file():
                sys.exit(f"[!] manifest references {name} but it is missing")
            shutil.copy2(s, stage / name)
        shutil.copy2(fuf, stage / "update.fuf")

        # --- rebrand: Info line = the on-device/update folder name ---
        info = f"{args.name}-{args.version}"
        new_entries = []
        for k, v in entries:
            if k == "Info":
                v = info
            new_entries.append((k, v))

        # --- optional splash swap (keeps the manifest's Splashscreen filename) ---
        if args.splash:
            sp = Path(args.splash).resolve()
            if not sp.is_file():
                sys.exit(f"[!] splash not found: {sp}")
            splash_name = next((v for k, v in new_entries if k == "Splashscreen"), "splash.bin")
            shutil.copy2(sp, stage / splash_name)
            print(f"[*] swapped splash -> {splash_name}")

        write_fuf(new_entries, stage / "update.fuf")
        print(f"[*] Info: {info}")

        # --- package as <name>-<version>/ inside a .tgz ---
        out_dir = Path(args.out).resolve()
        out_dir.mkdir(parents=True, exist_ok=True)
        folder = info
        tgz = out_dir / f"{folder}.tgz"
        with tarfile.open(tgz, "w:gz") as tar:
            for f in sorted(stage.iterdir()):
                tar.add(f, arcname=f"{folder}/{f.name}")
        digest = sha256(tgz)
        (out_dir / f"{folder}.tgz.sha256").write_text(f"{digest}  {tgz.name}\n")
        print(f"[+] wrote {tgz}  ({tgz.stat().st_size} bytes)")
        print(f"[+] sha256 {digest}")
        return 0
    finally:
        shutil.rmtree(stage, ignore_errors=True)


def main():
    ap = argparse.ArgumentParser(description="ZxmWare OTA bundle packer/rebrander")
    sub = ap.add_subparsers(dest="cmd", required=True)
    rb = sub.add_parser("rebrand", help="rebrand+repack an existing bundle dir")
    rb.add_argument("--src", required=True, help="dir containing update.fuf")
    rb.add_argument("--out", required=True, help="output dir (served by the update server)")
    rb.add_argument("--name", default="ZxmWare")
    rb.add_argument("--version", required=True, help="e.g. 0053 or 1.0.0")
    rb.add_argument("--splash", help="optional splash.bin to inject")
    rb.set_defaults(func=rebrand)
    args = ap.parse_args()
    raise SystemExit(args.func(args))


if __name__ == "__main__":
    main()
