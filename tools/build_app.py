#!/usr/bin/env python3
"""ZxmWare on-demand app builder.

Build a Flipper .fap from a source directory (ufbt, against the ZxmWare SDK),
stage it into the app repo, re-index, and optionally install it to the device.

    python3 tools/build_app.py apps_src/zxm_hello --install

Flow: ufbt build -> server/data/apps/<category>/<appid>.fap -> indexer -> RPC install.
The SDK must already be deployed:  ufbt update --local <ZxmWare SDK zip> -t f7
"""
import argparse
import os
import re
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PROJ = os.path.dirname(HERE)
APPS_DIR = os.path.join(PROJ, "server", "data", "apps")
sys.path.insert(0, HERE)
from zxm_build import build_fap, fam_meta  # noqa: E402


def main():
    ap = argparse.ArgumentParser(description="Build + stage + (optionally) install a ZxmWare app")
    ap.add_argument("app_dir", help="app source dir containing application.fam")
    ap.add_argument("--install", action="store_true", help="also push to the connected Flipper")
    ap.add_argument("--port", help="Flipper serial port (auto-detected if omitted)")
    ap.add_argument("--no-index", action="store_true", help="skip re-indexing the repo")
    args = ap.parse_args()

    app_dir = os.path.abspath(args.app_dir)
    appid, category = fam_meta(app_dir)
    if not appid:
        sys.exit("[!] application.fam has no appid")
    print(f"[*] app: {appid}  category: {category}")

    print("[*] building with ufbt (in a space-free temp dir)...")
    fap = build_fap(app_dir, quiet=False)
    if not fap or not os.path.isfile(fap):
        sys.exit("[!] no .fap produced (build failed)")

    dest = os.path.join(APPS_DIR, category, f"{appid}.fap")
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    shutil.copy2(fap, dest)
    print(f"[+] staged -> {os.path.relpath(dest, PROJ)}  ({os.path.getsize(dest)} B)")

    if not args.no_index:
        idx = os.path.join(PROJ, "server", "indexer", "index_repo.py")
        subprocess.run([sys.executable, "-I", idx, "--data-dir",
                        os.path.join(PROJ, "server", "data")], check=False)

    if args.install:
        sys.path.insert(0, HERE)
        from zxm_rpc import Flipper, find_port
        port = args.port or find_port()
        if not port:
            print("[!] no Flipper connected; staged on server only. Plug in and re-run with --install.")
            return 0
        on_device = f"/ext/apps/{category}/{appid}.fap"
        print(f"[*] installing to {on_device} on {port} ...")
        with Flipper(port) as fl:
            ok = fl.push_verified(dest, on_device)
        print("[+] installed + md5-verified on device." if ok else "[!] install failed md5 verify.")
    else:
        print("[i] not installed (no --install). It's in the repo + catalog; sync from the device to pull it.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
