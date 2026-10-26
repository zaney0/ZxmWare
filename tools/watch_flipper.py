#!/usr/bin/env python3
"""ZxmWare plug-in watcher.

Watches for the Flipper connecting over USB. When it appears, installs everything
queued in server/data/pending_install.json (md5-verified over RPC), clears what
installed, and sends a desktop notification. Designed to run under launchd.

    python3 tools/watch_flipper.py            # poll forever
    python3 tools/watch_flipper.py --once      # one check (for launchd StartInterval)
"""
import argparse
import json
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
PROJ = os.path.dirname(HERE)
PENDING = os.path.join(PROJ, "server", "data", "pending_install.json")
APPS_DIR = os.path.join(PROJ, "server", "data", "apps")
STAMP = os.path.join(HERE, ".watch_last_port")

sys.path.insert(0, HERE)
from zxm_rpc import Flipper, find_port  # noqa: E402


def log(*a):
    print("[watch]", *a, flush=True)


def notify(title, msg):
    try:
        subprocess.run(["osascript", "-e",
                        f'display notification "{msg}" with title "{title}"'],
                       check=False, capture_output=True)
    except Exception:
        pass


def load_pending():
    if not os.path.isfile(PENDING):
        return []
    try:
        return json.load(open(PENDING)).get("items", [])
    except Exception:
        return []


def save_pending(items):
    json.dump({"generated": int(time.time()), "items": items}, open(PENDING, "w"), indent=2)


def install_pending(port):
    items = load_pending()
    if not items:
        log("nothing pending.")
        return 0
    log(f"{len(items)} app(s) pending; installing...")
    remaining, installed = [], 0
    with Flipper(port) as fl:
        for e in items:
            # path like /apps/<Cat>/<file>.fap -> local + on-device
            rel = e["path"].lstrip("/")               # apps/<Cat>/<file>.fap
            local = os.path.join(os.path.dirname(APPS_DIR), rel)  # server/data/apps/...
            on_dev = "/ext/" + rel                     # /ext/apps/<Cat>/<file>.fap
            if not os.path.isfile(local):
                log(f"  missing local {rel}; skipping")
                continue
            try:
                ok = fl.push_verified(local, on_dev)
            except Exception as ex:
                log(f"  error {rel}: {ex}")
                ok = False
            if ok:
                installed += 1
                log(f"  + {rel}")
            else:
                remaining.append(e)
    save_pending(remaining)
    if installed:
        notify("ZxmWare", f"Installed {installed} app(s) to your Flipper.")
    return installed


def check_once():
    port = find_port()
    last = open(STAMP).read().strip() if os.path.isfile(STAMP) else ""
    if not port:
        if last:
            open(STAMP, "w").write("")  # device unplugged; reset edge
        return
    if port == last:
        return  # already handled this connection
    log(f"Flipper connected: {port}")
    time.sleep(2)  # let it settle
    try:
        install_pending(port)
        open(STAMP, "w").write(port)
    except Exception as ex:
        log("install run failed:", ex)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true", help="single check then exit (for launchd)")
    ap.add_argument("--interval", type=int, default=5, help="poll seconds (daemon mode)")
    args = ap.parse_args()
    if args.once:
        check_once()
        return 0
    log("watching for Flipper (Ctrl-C to stop)...")
    while True:
        try:
            check_once()
        except Exception as ex:
            log("loop error:", ex)
        time.sleep(args.interval)


if __name__ == "__main__":
    raise SystemExit(main())
