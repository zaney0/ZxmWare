#!/usr/bin/env python3
"""ZxmWare auto-curator.

Pulls Flipper app sources (local dirs + trusted git repos), builds each with
ufbt against the ZxmWare SDK (so they match the firmware on the device), stages
new/updated .faps into the repo, re-indexes, queues them for install, and sends a
desktop notification telling you to plug in.

    python3 tools/curate.py            # run once
    (scheduled via launchd -- see tools/automation/)

SECURITY: git sources are CLONED, COMPILED and will RUN on your device. Only add
repos you trust to tools/curate_sources.json.
"""
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
PROJ = os.path.dirname(HERE)
APPS_DIR = os.path.join(PROJ, "server", "data", "apps")
# cache lives OUTSIDE the (possibly space-containing) project path
CACHE = os.path.join(os.path.expanduser("~"), ".zxmware", "curate_cache")
SOURCES = os.path.join(HERE, "curate_sources.json")
PENDING = os.path.join(PROJ, "server", "data", "pending_install.json")

sys.path.insert(0, HERE)
from zxm_build import build_fap, fam_meta  # noqa: E402


def log(*a):
    print("[curate]", *a, flush=True)


def notify(title, msg):
    """Best-effort macOS desktop notification."""
    try:
        subprocess.run(
            ["osascript", "-e", f'display notification "{msg}" with title "{title}"'],
            check=False, capture_output=True)
    except Exception:
        pass


def sha256(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for c in iter(lambda: f.read(1 << 20), b""):
            h.update(c)
    return h.hexdigest()


def load_sources():
    if not os.path.isfile(SOURCES):
        return []
    return json.load(open(SOURCES)).get("sources", [])


def ensure_source(src):
    """Return a local path for a source (clone/pull git, or use local path)."""
    if src.get("type") == "local":
        return os.path.join(PROJ, src["path"]) if not os.path.isabs(src["path"]) else src["path"]
    if src.get("type") == "git":
        os.makedirs(CACHE, exist_ok=True)
        name = re.sub(r"[^A-Za-z0-9_.-]", "_", src["url"])
        dest = os.path.join(CACHE, name)
        if os.path.isdir(os.path.join(dest, ".git")):
            subprocess.run(["git", "-C", dest, "pull", "--ff-only"], check=False,
                           capture_output=True)
        else:
            subprocess.run(["git", "clone", "--depth", "1"] +
                           (["-b", src["ref"]] if src.get("ref") else []) +
                           [src["url"], dest], check=False, capture_output=True)
        return os.path.join(dest, src["subdir"]) if src.get("subdir") else dest
    return None


def find_apps(root):
    """Yield dirs that contain an EXTERNAL application.fam."""
    for dirpath, _dirs, files in os.walk(root):
        if "application.fam" in files:
            txt = open(os.path.join(dirpath, "application.fam"), errors="ignore").read()
            if "EXTERNAL" in txt and "appid" in txt:
                yield dirpath


def main():
    sources = load_sources()
    if not sources:
        log("no sources configured in tools/curate_sources.json; nothing to do.")
        return 0
    log(f"{len(sources)} source(s)")
    new_entries = []
    for src in sources:
        root = ensure_source(src)
        if not root or not os.path.isdir(root):
            log(f"  skip unreachable source: {src}")
            continue
        for app_dir in find_apps(root):
            appid, category = fam_meta(app_dir)
            if not appid:
                continue
            fap = build_fap(app_dir)   # builds in a space-free temp dir
            if not fap:
                log(f"  build failed/skipped: {os.path.basename(app_dir)}")
                continue
            dest = os.path.join(APPS_DIR, category, f"{appid}.fap")
            is_new = not os.path.isfile(dest) or sha256(fap) != sha256(dest)
            if is_new:
                os.makedirs(os.path.dirname(dest), exist_ok=True)
                shutil.copy2(fap, dest)
                rel = f"/apps/{category}/{appid}.fap"
                new_entries.append({"id": appid, "category": category, "path": rel})
                log(f"  + {category}/{appid}.fap")

    # re-index
    subprocess.run([sys.executable, "-I", os.path.join(PROJ, "server", "indexer", "index_repo.py"),
                    "--data-dir", os.path.join(PROJ, "server", "data")], check=False,
                   capture_output=True)

    # queue pending installs (merge, de-dupe by path)
    pending = []
    if os.path.isfile(PENDING):
        try:
            pending = json.load(open(PENDING)).get("items", [])
        except Exception:
            pending = []
    seen = {e["path"] for e in pending}
    for e in new_entries:
        if e["path"] not in seen:
            pending.append(e)
    json.dump({"generated": int(time.time()), "items": pending}, open(PENDING, "w"), indent=2)

    if new_entries:
        n = len(new_entries)
        log(f"{n} new/updated app(s) queued.")
        notify("ZxmWare", f"{n} new app(s) ready - plug in your Flipper to install.")
    else:
        log("no new apps.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
