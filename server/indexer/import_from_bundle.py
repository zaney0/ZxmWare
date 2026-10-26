#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
import_from_bundle.py -- seed the ZxmWare app repo from an existing OTA release.

A firmware release ships its on-device SD payload as `resources.tar`, which
contains (among much else) the pre-built user apps under `apps/<Category>/*.fap`.
Rather than ask maintainers to track down every .fap separately, this helper
pulls those .fap files out of a release you already have and drops them into
`server/data/apps/<Category>/` so the server can serve each app individually
(and so `index_repo.py` can catalog them).

The input can be:
  * a `resources.tar` file directly, or
  * a firmware bundle `.tgz` (we locate resources.tar inside it), or
  * a directory -- we look for `resources.tar` directly in it, and if not found,
    for any `*.tgz` bundle in it.

Everything is read in memory; extraction is path-traversal safe (entries that
escape `apps/` or contain `..` / absolute paths are skipped). Use --dry-run to
see what would be copied without writing anything.

Usage examples:

    python3 -I import_from_bundle.py ../data/firmware/ZxmWare-0053.tgz
    python3 -I import_from_bundle.py /path/to/resources.tar
    python3 -I import_from_bundle.py /path/to/release_dir --dry-run
    python3 -I import_from_bundle.py bundle.tgz --apps-dir /srv/zxmware/data/apps
    python3 -I import_from_bundle.py bundle.tgz --force   # overwrite existing
"""

import argparse
import os
import posixpath
import sys
import tarfile

# The in-bundle prefix under which Flipper user apps live.
APPS_PREFIX = "apps/"

# Known categories (for a gentle warning on anything unexpected). Not enforced.
KNOWN_CATEGORIES = (
    "Bluetooth", "GPIO", "Games", "Infrared", "Media", "NFC", "RFID",
    "Sub-GHz", "Tools", "USB", "WiFi", "iButton",
)

_COPY_CHUNK = 1024 * 1024


def default_apps_dir():
    """Resolve ../data/apps relative to this script, as an absolute path."""
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.abspath(os.path.join(here, "..", "data", "apps"))


# ---------------------------------------------------------------------------
# Path-traversal safety
# ---------------------------------------------------------------------------

def is_safe_apps_member(member_name):
    """
    Decide whether a tar member is a safe `apps/<Category>/<file>.fap` entry.

    Rejects absolute paths, any component equal to `..`, drive-letter / UNC
    weirdness, and anything not actually under `apps/`. We normalize using
    posixpath because tar member names are always forward-slash separated.

    Returns the normalized relative-to-`apps/` path on success, else None.
    """
    name = member_name

    # Absolute paths (unix or windows) are never allowed.
    if name.startswith("/") or name.startswith("\\"):
        return None
    if len(name) >= 2 and name[1] == ":":  # e.g. "C:..."
        return None

    # Normalize separators and collapse . / .. where possible.
    name = name.replace("\\", "/")
    norm = posixpath.normpath(name)

    # After normalization, reject anything that tries to climb out.
    if norm == ".." or norm.startswith("../") or "/../" in norm:
        return None
    if norm.startswith("/"):
        return None

    # Must live under apps/.
    if not (norm == "apps" or norm.startswith(APPS_PREFIX)):
        return None

    # The relative path beneath apps/.
    rel = norm[len(APPS_PREFIX):] if norm.startswith(APPS_PREFIX) else ""
    return rel  # may be "" for the apps/ dir itself


# ---------------------------------------------------------------------------
# Locating resources.tar
# ---------------------------------------------------------------------------

def _find_member(tf, basename):
    """First regular-file member whose basename matches `basename`, else None."""
    for member in tf.getmembers():
        if member.isfile() and posixpath.basename(member.name) == basename:
            return member
    return None


def open_resources_tar(input_path):
    """
    Return an open tarfile.TarFile positioned at the resources.tar contents,
    plus a label describing where it came from. Caller must close the returned
    TarFile. On failure returns (None, reason_string).

    Handles the three accepted input shapes (resources.tar, firmware .tgz,
    directory). Nested tars are read via extractfile and wrapped without being
    written to disk.
    """
    if os.path.isdir(input_path):
        # Prefer a bare resources.tar, else the first *.tgz bundle.
        direct = os.path.join(input_path, "resources.tar")
        if os.path.isfile(direct):
            return _open_plain_tar(direct)
        tgzs = sorted(n for n in os.listdir(input_path) if n.endswith(".tgz"))
        if tgzs:
            return _open_resources_from_tgz(os.path.join(input_path, tgzs[0]))
        return None, "directory has no resources.tar or *.tgz bundle"

    if not os.path.isfile(input_path):
        return None, "no such file: %s" % input_path

    base = os.path.basename(input_path)
    if base == "resources.tar" or input_path.endswith(".tar"):
        return _open_plain_tar(input_path)
    if input_path.endswith(".tgz") or input_path.endswith(".tar.gz"):
        return _open_resources_from_tgz(input_path)

    # Fall back to probing: maybe it's a tar of some kind.
    try:
        tf = tarfile.open(input_path, mode="r:*")
    except tarfile.TarError as exc:
        return None, "not a recognized bundle/tar: %s (%s)" % (input_path, exc)
    # If it directly contains apps/, treat it as a resources tar.
    return tf, input_path


def _open_plain_tar(path):
    try:
        return tarfile.open(path, mode="r:*"), path
    except (tarfile.TarError, OSError) as exc:
        return None, "cannot open tar %s (%s)" % (path, exc)


def _open_resources_from_tgz(tgz_path):
    """
    Open a firmware .tgz, find resources.tar inside it, and return a TarFile
    over that nested tar. The nested stream is fully buffered in memory.
    """
    import io
    try:
        with tarfile.open(tgz_path, mode="r:*") as outer:
            res_member = _find_member(outer, "resources.tar")
            if res_member is None:
                return None, "no resources.tar inside %s" % tgz_path
            stream = outer.extractfile(res_member)
            if stream is None:
                return None, "could not read resources.tar inside %s" % tgz_path
            with stream:
                buf = io.BytesIO(stream.read())
        return tarfile.open(fileobj=buf, mode="r:*"), "%s!resources.tar" % tgz_path
    except (tarfile.TarError, OSError) as exc:
        return None, "cannot read bundle %s (%s)" % (tgz_path, exc)


# ---------------------------------------------------------------------------
# The copy operation
# ---------------------------------------------------------------------------

def collect_fap_members(tf):
    """
    Yield (member, rel_under_apps) for each safe `apps/<Category>/<x>.fap` file
    in the tar. Unsafe or non-.fap entries are skipped.
    """
    for member in tf.getmembers():
        if not member.isfile():
            continue
        rel = is_safe_apps_member(member.name)
        if rel is None or rel == "":
            continue
        if not rel.endswith(".fap"):
            continue
        # We expect <Category>/<file>.fap -- exactly one directory level.
        if "/" not in rel:
            # A .fap sitting directly in apps/ has no category; skip it.
            continue
        yield member, rel


def import_apps(input_path, apps_dir, dry_run=False, force=False, out=sys.stdout):
    """
    Extract apps from a bundle/resources.tar into apps_dir/<Category>/.

    Returns an exit code (0 ok, non-zero on error). Prints a line per app that
    is (or would be) copied, and a trailing summary.
    """
    tf, label = open_resources_tar(input_path)
    if tf is None:
        print("error: %s" % label, file=sys.stderr)
        return 1

    copied = 0
    skipped_exist = 0
    unknown_cats = set()

    try:
        print("source: %s" % label, file=out)
        apps_dir = os.path.abspath(apps_dir)

        for member, rel in collect_fap_members(tf):
            category = rel.split("/", 1)[0]
            if category not in KNOWN_CATEGORIES:
                unknown_cats.add(category)

            dest_path = os.path.join(apps_dir, *rel.split("/"))
            # Defense-in-depth: ensure the resolved dest stays under apps_dir.
            if not _within(apps_dir, dest_path):
                print("  SKIP (escapes apps dir): %s" % member.name, file=out)
                continue

            exists = os.path.exists(dest_path)
            if exists and not force:
                print("  skip (exists): apps/%s" % rel, file=out)
                skipped_exist += 1
                continue

            verb = "would copy" if dry_run else ("overwrite" if exists else "copy")
            print("  %s: apps/%s (%d bytes)" % (verb, rel, member.size), file=out)

            if not dry_run:
                os.makedirs(os.path.dirname(dest_path), exist_ok=True)
                src = tf.extractfile(member)
                if src is None:
                    print("    ! could not read member, skipping", file=out)
                    continue
                with src, open(dest_path, "wb") as dst:
                    for chunk in iter(lambda: src.read(_COPY_CHUNK), b""):
                        dst.write(chunk)
            copied += 1
    finally:
        tf.close()

    # Summary.
    print("", file=out)
    if dry_run:
        print("DRY RUN -- nothing written.", file=out)
    print("apps dir: %s" % apps_dir, file=out)
    print("%s %d app(s); skipped %d existing." %
          ("would copy" if dry_run else "copied", copied, skipped_exist), file=out)
    if unknown_cats:
        print("note: unexpected categor(y/ies): %s" % ", ".join(sorted(unknown_cats)),
              file=out)
    return 0


def _within(parent, child):
    """True if `child` resolves to a path inside `parent`."""
    parent = os.path.abspath(parent)
    child = os.path.abspath(child)
    try:
        return os.path.commonpath([parent, child]) == parent
    except ValueError:
        # Different drives on Windows, etc.
        return False


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_arg_parser():
    p = argparse.ArgumentParser(
        prog="import_from_bundle.py",
        description="Seed server/data/apps/ from a firmware release's resources.tar.",
    )
    p.add_argument(
        "source",
        help="A resources.tar, a firmware .tgz bundle, or a directory containing either.",
    )
    p.add_argument(
        "--apps-dir",
        default=default_apps_dir(),
        help="Destination apps directory (default: ../data/apps relative to this script).",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be copied without writing any files.",
    )
    p.add_argument(
        "--force",
        action="store_true",
        help="Overwrite .fap files that already exist in the destination.",
    )
    return p


def main(argv=None):
    args = build_arg_parser().parse_args(argv)
    source = os.path.abspath(args.source)
    return import_apps(
        input_path=source,
        apps_dir=args.apps_dir,
        dry_run=args.dry_run,
        force=args.force,
    )


if __name__ == "__main__":
    sys.exit(main())
