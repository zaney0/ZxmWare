#!/usr/bin/env python3
"""Shared ufbt build helper for ZxmWare tooling.

Builds a Flipper app in a space-free temp directory, because one external app's
asset compiler (air_arkanoid's sprite_builder.py, and others like it) does not
quote paths and breaks when the path contains spaces (e.g. ".../AI Cli Projects/...").
Copying the app to ~/.zxmware/build_tmp/<name> first sidesteps that for ALL apps.
"""
import os
import re
import shutil
import subprocess
import sys

BUILD_TMP = os.path.join(os.path.expanduser("~"), ".zxmware", "build_tmp")
UFBT = os.path.join(os.path.dirname(sys.executable), "ufbt")


def fam_meta(app_dir):
    """Return (appid, category) from application.fam."""
    txt = open(os.path.join(app_dir, "application.fam"), errors="ignore").read()
    appid = re.search(r'appid\s*=\s*"([^"]+)"', txt)
    cat = re.search(r'fap_category\s*=\s*"([^"]+)"', txt)
    return (appid.group(1) if appid else None, cat.group(1) if cat else "Misc")


def build_fap(app_dir, quiet=True):
    """Build `app_dir` in a space-free temp copy. Return path to the .fap or None."""
    appid, _ = fam_meta(app_dir)
    if not appid:
        return None
    os.makedirs(BUILD_TMP, exist_ok=True)
    work = os.path.join(BUILD_TMP, appid)
    shutil.rmtree(work, ignore_errors=True)
    # copy sources only (skip any prior dist/.ufbt/.vscode)
    shutil.copytree(app_dir, work, ignore=shutil.ignore_patterns(
        "dist", ".ufbt", ".vscode", "build", "*.fap"))
    kw = dict(capture_output=True, text=True) if quiet else {}
    r = subprocess.run([UFBT], cwd=work, **kw)
    if r.returncode != 0:
        if quiet and r.stderr:
            sys.stderr.write(r.stderr[-800:])
        return None
    dist = os.path.join(work, "dist")
    faps = [f for f in os.listdir(dist) if f.endswith(".fap")] if os.path.isdir(dist) else []
    return os.path.join(dist, faps[0]) if faps else None
