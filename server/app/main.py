#!/usr/bin/env python3
"""
ZxmWare API server.

A tiny, self-hosted distribution server for the Flipper Zero that serves:

  * Firmware OTA bundles  -  data/firmware/<Name>-<ver>.tgz  (+ sibling .sha256)
  * A curated .fap app repo - data/apps/<Category>/<name>.fap

A separate indexer process may pre-compute:
  * data/firmware/directory.json
  * data/apps/directory.json
If those files exist they are served verbatim (after re-basing their URLs onto the
incoming request so the companion app always gets absolute, reachable links). If they
are missing, this server computes an equivalent listing live by scanning the data dirs.

Configuration:
  DATA_DIR   root of the served content tree (default: <repo>/server/data)
             Must contain `firmware/` and `apps/` subdirectories.

Run:
  uvicorn app.main:app --host 0.0.0.0 --port 8000
"""
from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse

# --------------------------------------------------------------------------- #
# Constants / configuration
# --------------------------------------------------------------------------- #

SERVER_NAME = "ZxmWare"
SERVER_VERSION = "1.0.0"

# Valid firmware channels. The default channel is "release".
CHANNELS = ("release", "dev")
DEFAULT_CHANNEL = "release"

# Real Flipper .fap categories (used only to validate scanned subdirectories exist;
# any directory under apps/ is treated as a category, this is just documentation).
KNOWN_CATEGORIES = (
    "Bluetooth", "GPIO", "Games", "Infrared", "Media", "NFC",
    "RFID", "Sub-GHz", "Tools", "USB", "WiFi", "iButton",
)


def _default_data_dir() -> Path:
    """Default DATA_DIR is the repo's server/data (this file lives at server/app/main.py)."""
    return Path(__file__).resolve().parent.parent / "data"


DATA_DIR = Path(os.environ.get("DATA_DIR", str(_default_data_dir()))).resolve()
FIRMWARE_DIR = DATA_DIR / "firmware"
APPS_DIR = DATA_DIR / "apps"

# Chunk size for hashing large files.
_HASH_CHUNK = 1 << 20  # 1 MiB


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

def sha256_file(path: Path) -> str:
    """Streamed SHA-256 so we never load a whole bundle into memory."""
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(_HASH_CHUNK), b""):
            h.update(chunk)
    return h.hexdigest()


def read_sidecar_sha256(path: Path) -> str | None:
    """
    Return the hash recorded in a sibling `<file>.sha256` if present.

    The sidecar is accepted in either the bare-hex form or the `<hash>  <name>`
    form produced by the `sha256sum` tool. Returns None if there is no sidecar or
    it cannot be parsed, in which case the caller should hash the file directly.
    """
    sidecar = path.with_name(path.name + ".sha256")
    if not sidecar.is_file():
        return None
    try:
        first = sidecar.read_text(encoding="utf-8", errors="replace").strip().split()
    except OSError:
        return None
    if not first:
        return None
    token = first[0].strip()
    # A valid sha256 is 64 hex chars.
    if len(token) == 64 and all(c in "0123456789abcdefABCDEF" for c in token):
        return token.lower()
    return None


def file_timestamp(path: Path) -> str:
    """ISO-8601 UTC timestamp of a file's mtime."""
    ts = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
    return ts.isoformat().replace("+00:00", "Z")


def base_url(request: Request) -> str:
    """
    Absolute base URL of this server as seen by the caller, honouring reverse-proxy
    headers (X-Forwarded-Proto / X-Forwarded-Host) so links are reachable from the
    companion app even behind a TLS terminator. No trailing slash.
    """
    proto = request.headers.get("x-forwarded-proto")
    host = request.headers.get("x-forwarded-host")
    if proto and host:
        return f"{proto.split(',')[0].strip()}://{host.split(',')[0].strip()}"
    # Starlette's request.base_url already resolves scheme/host/port.
    return str(request.base_url).rstrip("/")


def safe_segment(segment: str) -> str:
    """
    Validate a single user-supplied path segment (a filename or one directory name).

    Rejects empty names, absolute paths, any path separators, and traversal via `..`.
    Raises HTTP 400 on anything suspicious. Returns the segment unchanged on success.
    """
    if not segment or segment in (".", ".."):
        raise HTTPException(status_code=400, detail="Invalid path segment")
    # No separators of any flavour, no NUL, no drive letters, no leading slash.
    if "/" in segment or "\\" in segment or "\x00" in segment:
        raise HTTPException(status_code=400, detail="Invalid path segment")
    if os.path.isabs(segment) or ".." in Path(segment).parts:
        raise HTTPException(status_code=400, detail="Invalid path segment")
    return segment


def resolve_under(root: Path, *segments: str) -> Path:
    """
    Join validated segments under `root` and confirm the fully-resolved path is still
    inside `root` (defence in depth against symlink / traversal tricks).
    """
    for seg in segments:
        safe_segment(seg)
    candidate = root.joinpath(*segments).resolve()
    root_resolved = root.resolve()
    if candidate != root_resolved and root_resolved not in candidate.parents:
        raise HTTPException(status_code=400, detail="Path escapes data directory")
    return candidate


def channel_for_firmware(path: Path) -> str:
    """
    Determine a firmware bundle's release channel from filename / directory convention.

    Convention:
      * A bundle living under a `dev/` subdirectory  -> "dev".
      * A filename whose version token contains `dev`, `rc`, `beta` or `alpha`
        (e.g. ZxmWare-0053-dev.tgz, ZxmWare-1.2.0-rc1.tgz) -> "dev".
      * Everything else -> "release".
    """
    rel_parts = {p.lower() for p in path.relative_to(FIRMWARE_DIR).parts[:-1]}
    if "dev" in rel_parts:
        return "dev"
    stem = path.stem.lower()  # strips .tgz
    if any(tag in stem for tag in ("-dev", ".dev", "-rc", "-beta", "-alpha", "nightly")):
        return "dev"
    return "release"


def parse_version(filename: str) -> str:
    """
    Pull the version token out of a `<Name>-<ver>.tgz` filename.

    `ZxmWare-0053.tgz` -> `0053`, `ZxmWare-1.2.0-dev.tgz` -> `1.2.0-dev`.
    Falls back to the whole stem if there is no dash.
    """
    stem = filename
    for suffix in (".tgz", ".tar.gz"):
        if stem.endswith(suffix):
            stem = stem[: -len(suffix)]
            break
    if "-" in stem:
        return stem.split("-", 1)[1]
    return stem


# --------------------------------------------------------------------------- #
# Live scanning (fallbacks when the indexer's directory.json is absent)
# --------------------------------------------------------------------------- #

def scan_firmware(request: Request) -> dict[str, Any]:
    """
    Build a firmware index by scanning *.tgz under FIRMWARE_DIR.

    Returns the canonical schema the indexer and both clients use:
        {"name", "generated", "channels": {"release": [...], "dev": [...]}}
    Each entry carries a relative `path` (clients prepend the server base) plus an
    absolute `url` for convenience.
    """
    base = base_url(request)
    channels: dict[str, list[dict[str, Any]]] = {c: [] for c in CHANNELS}

    if FIRMWARE_DIR.is_dir():
        for tgz in sorted(FIRMWARE_DIR.rglob("*.tgz")):
            if not tgz.is_file():
                continue
            ch = channel_for_firmware(tgz)
            if ch not in channels:
                channels[ch] = []
            # The bare filename resolves via /firmware/{filename} regardless of which
            # channel subdir the bundle physically lives in.
            filename = tgz.name
            channels[ch].append(
                {
                    "version": parse_version(filename),
                    "file": filename,
                    "path": f"/firmware/{filename}",
                    "url": f"{base}/firmware/{filename}",
                    "sha256": read_sidecar_sha256(tgz) or sha256_file(tgz),
                    "size": tgz.stat().st_size,
                    "timestamp": file_timestamp(tgz),
                    "channel": ch,
                }
            )

    # Newest first within each channel.
    for entries in channels.values():
        entries.sort(key=lambda e: e["timestamp"], reverse=True)
    return {
        "name": SERVER_NAME,
        "generated": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "channels": channels,
    }


def scan_apps(request: Request) -> dict[str, Any]:
    """
    Build the .fap catalog by scanning APPS_DIR/<Category>/<name>.fap.

    Returns the canonical schema the indexer and both clients use:
        {"name", "generated", "categories": {"<Cat>": [ {id,name,category,file,path,size,sha256}, ... ]}}
    """
    base = base_url(request)
    categories: dict[str, list[dict[str, Any]]] = {}

    if APPS_DIR.is_dir():
        for fap in sorted(APPS_DIR.rglob("*.fap")):
            if not fap.is_file():
                continue
            rel = fap.relative_to(APPS_DIR)
            if len(rel.parts) < 2:
                # Expected layout is <Category>/<name>.fap; skip loose files.
                continue
            category = rel.parts[0]
            filename = fap.name
            categories.setdefault(category, []).append(
                {
                    "id": fap.stem,
                    "name": fap.stem,
                    "category": category,
                    "file": filename,
                    "path": f"/apps/{category}/{filename}",
                    "url": f"{base}/apps/{category}/{filename}",
                    "size": fap.stat().st_size,
                    "sha256": read_sidecar_sha256(fap) or sha256_file(fap),
                }
            )

    for entries in categories.values():
        entries.sort(key=lambda e: e["name"].lower())
    return {
        "name": SERVER_NAME,
        "generated": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "categories": dict(sorted(categories.items())),
    }


# --------------------------------------------------------------------------- #
# Serving a pre-built directory.json, re-basing its URLs onto this request
# --------------------------------------------------------------------------- #

def _rebase_urls(obj: Any, base: str) -> Any:
    """
    Recursively rewrite any `url` string in a loaded directory.json so it is absolute
    against THIS request's base. Accepts entries whose `url` is already absolute (left
    as-is unless they point at a different host path we can normalise) or relative.
    """
    if isinstance(obj, dict):
        new = {}
        for k, v in obj.items():
            if k == "url" and isinstance(v, str):
                new[k] = _absolute_url(v, base)
            else:
                new[k] = _rebase_urls(v, base)
        return new
    if isinstance(obj, list):
        return [_rebase_urls(v, base) for v in obj]
    return obj


def _absolute_url(url: str, base: str) -> str:
    """Make a possibly-relative indexer URL absolute against `base`."""
    if url.startswith(("http://", "https://")):
        # Already absolute; trust the indexer's path but swap in our host so links
        # work when the server is reached via a different name than the indexer saw.
        path = url.split("//", 1)[1]
        path = path[path.find("/"):] if "/" in path else "/"
        return f"{base}{path}"
    if not url.startswith("/"):
        url = "/" + url
    return f"{base}{url}"


def load_prebuilt(path: Path, request: Request) -> dict[str, Any] | None:
    """Load a pre-built directory.json and re-base its URLs; None if absent/invalid."""
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return _rebase_urls(data, base_url(request))


# --------------------------------------------------------------------------- #
# FastAPI app
# --------------------------------------------------------------------------- #

app = FastAPI(title=f"{SERVER_NAME} API", version=SERVER_VERSION)

# The Web Serial installer page is served from a different origin and must fetch the
# directory + bundles cross-origin, so CORS is permissive. These are public,
# read-only assets, so this is safe.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "HEAD", "OPTIONS"],
    allow_headers=["*"],
    expose_headers=["Content-Length", "Content-Disposition", "ETag"],
)


@app.get("/health")
def health() -> dict[str, str]:
    """Liveness probe used by docker-compose / monitoring."""
    return {"status": "ok", "name": SERVER_NAME, "version": SERVER_VERSION}


@app.get("/firmware/directory.json")
def firmware_directory(
    request: Request,
    channel: str | None = Query(None, description="optional filter: release | dev"),
) -> JSONResponse:
    """
    Firmware channel index in the canonical schema:
        {"name", "generated", "channels": {"release": [...], "dev": [...]}}
    Serves data/firmware/directory.json if the indexer wrote one, else scans live.
    `?channel=` is an OPTIONAL narrowing filter; without it, all channels are returned
    (clients read `.channels.release` / `.channels.dev` themselves).
    """
    if channel is not None and channel not in CHANNELS:
        raise HTTPException(status_code=400, detail=f"Unknown channel '{channel}'")

    prebuilt = load_prebuilt(FIRMWARE_DIR / "directory.json", request)
    data = prebuilt if prebuilt is not None else scan_firmware(request)

    # Accept the canonical {"channels": {...}} shape; tolerate a legacy flat
    # {"firmware": [...]} index by regrouping it into channels.
    channels = data.get("channels")
    if channels is None and isinstance(data.get("firmware"), list):
        channels = {}
        for e in data["firmware"]:
            channels.setdefault(str(e.get("channel", DEFAULT_CHANNEL)), []).append(e)
        data = dict(data)
        data["channels"] = channels
        data.pop("firmware", None)

    if channel is not None and isinstance(channels, dict):
        data = dict(data)
        data["channels"] = {channel: channels.get(channel, [])}

    return JSONResponse(data)


@app.get("/firmware/{filename}")
def get_firmware(request: Request, filename: str) -> FileResponse:
    """
    Stream a firmware bundle (.tgz) or its checksum sidecar (.sha256) from
    data/firmware. Searches channel subdirectories too, so the directory URL's bare
    filename resolves wherever the bundle physically lives. 404 if not found.
    """
    safe_segment(filename)
    if not (filename.endswith(".tgz") or filename.endswith(".sha256")
            or filename.endswith(".tar.gz")):
        raise HTTPException(status_code=400, detail="Unsupported firmware file type")

    # Fast path: directly under FIRMWARE_DIR.
    direct = resolve_under(FIRMWARE_DIR, filename)
    target: Path | None = direct if direct.is_file() else None

    # Otherwise search channel subdirs for a matching basename.
    if target is None and FIRMWARE_DIR.is_dir():
        for match in FIRMWARE_DIR.rglob(filename):
            resolved = match.resolve()
            # Keep traversal safety: must stay under FIRMWARE_DIR.
            if match.is_file() and FIRMWARE_DIR.resolve() in resolved.parents:
                target = resolved
                break

    if target is None or not target.is_file():
        raise HTTPException(status_code=404, detail="Firmware not found")

    media_type = "text/plain" if filename.endswith(".sha256") else "application/gzip"
    return FileResponse(
        target,
        media_type=media_type,
        filename=filename,
        headers={"Cache-Control": "public, max-age=3600"},
    )


@app.get("/apps/directory.json")
def apps_directory(request: Request) -> JSONResponse:
    """
    Catalog of .fap apps. Serves data/apps/directory.json if present, else scans
    data/apps/<Category>/<name>.fap live.
    """
    prebuilt = load_prebuilt(APPS_DIR / "directory.json", request)
    if prebuilt is not None:
        return JSONResponse(prebuilt)
    return JSONResponse(scan_apps(request))


@app.get("/apps/{category}/{filename}")
def get_app(request: Request, category: str, filename: str) -> FileResponse:
    """Stream a single .fap. Both path segments are traversal-validated."""
    safe_segment(category)
    safe_segment(filename)
    if not filename.endswith(".fap"):
        raise HTTPException(status_code=400, detail="Not a .fap file")

    target = resolve_under(APPS_DIR, category, filename)
    if not target.is_file():
        raise HTTPException(status_code=404, detail="App not found")

    return FileResponse(
        target,
        media_type="application/octet-stream",
        filename=filename,
        headers={"Cache-Control": "public, max-age=3600"},
    )


@app.get("/", response_class=HTMLResponse)
def landing(request: Request) -> HTMLResponse:
    """Minimal landing page: live counts + link to the Web Serial installer."""
    base = base_url(request)

    # Cheap counts without hashing (don't call the scanners, which would hash files).
    fw_count = len([p for p in FIRMWARE_DIR.rglob("*.tgz")]) if FIRMWARE_DIR.is_dir() else 0
    app_count = len([p for p in APPS_DIR.rglob("*.fap")]) if APPS_DIR.is_dir() else 0

    html = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{SERVER_NAME}</title>
<style>
  :root {{ color-scheme: light dark; }}
  body {{ font-family: system-ui, -apple-system, Segoe UI, Roboto, sans-serif;
         max-width: 42rem; margin: 3rem auto; padding: 0 1rem; line-height: 1.5; }}
  h1 {{ margin-bottom: 0.25rem; }}
  code {{ background: rgba(127,127,127,.18); padding: .1rem .35rem; border-radius: .25rem; }}
  .counts {{ display: flex; gap: 1.5rem; margin: 1.5rem 0; }}
  .counts div {{ font-size: 2rem; font-weight: 700; }}
  .counts span {{ display: block; font-size: .8rem; font-weight: 400; opacity: .7; }}
  ul {{ padding-left: 1.2rem; }}
  a {{ color: #ff7a18; }}
</style>
</head>
<body>
  <h1>{SERVER_NAME}</h1>
  <p>Self-hosted custom-firmware &amp; app server for the Flipper Zero.</p>
  <div class="counts">
    <div>{fw_count}<span>firmware bundles</span></div>
    <div>{app_count}<span>.fap apps</span></div>
  </div>
  <p><a href="/installer/">Open the Web Serial installer &rarr;</a></p>
  <h2>API</h2>
  <ul>
    <li><code>GET /health</code></li>
    <li><a href="/firmware/directory.json"><code>GET /firmware/directory.json?channel=release</code></a></li>
    <li><code>GET /firmware/&lt;file&gt;.tgz</code></li>
    <li><a href="/apps/directory.json"><code>GET /apps/directory.json</code></a></li>
    <li><code>GET /apps/&lt;Category&gt;/&lt;name&gt;.fap</code></li>
  </ul>
  <p>Point the companion installer at: <code>BASE_URL={base}</code></p>
</body>
</html>"""
    return HTMLResponse(html)
