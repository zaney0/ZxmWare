# ZxmWare Server

Self-hosted custom-firmware distribution + `.fap` package server for the
[Flipper Zero](https://flipperzero.one/). It serves firmware OTA bundles and a curated
repository of Flipper Application Packages over a tiny HTTP API that the ZxmWare
companion app and the browser-based Web Serial installer consume.

## Run it

```bash
cd server
docker compose up -d        # builds the image and starts the server
docker compose logs -f      # watch logs
```

The API is then available on <http://localhost:8000> (health check at `/health`).

To stop: `docker compose down`.

> The large firmware/app content is **not** baked into the image. It lives in
> `./data` on the host and is bind-mounted into the container at `/srv/data`
> (read-only). Add or remove files on the host and they show up immediately — no
> rebuild required.

## Where to put content

```
server/data/
├── firmware/
│   ├── ZxmWare-0053.tgz          # a release bundle
│   ├── ZxmWare-0053.tgz.sha256   # sibling checksum (optional but recommended)
│   ├── dev/
│   │   └── ZxmWare-0054-dev.tgz  # a dev-channel bundle
│   └── directory.json            # (optional) pre-built index from the indexer
└── apps/
    ├── NFC/
    │   └── mfkey.fap
    ├── Sub-GHz/
    │   └── subbrute.fap
    └── directory.json            # (optional) pre-built catalog from the indexer
```

- **Firmware** bundles are named `<Name>-<version>.tgz` and live in `data/firmware/`.
  Drop a sibling `<file>.tgz.sha256` next to each one (as produced by `build/pack_update.py`)
  and the server uses it directly; otherwise it hashes the bundle on the fly.
- **Channels**: a bundle is treated as **dev** if it sits under a `dev/` subdirectory
  *or* its version token contains `dev`, `rc`, `beta`, `alpha`, or `nightly`
  (e.g. `ZxmWare-0054-dev.tgz`). Everything else is **release**.
- **Apps** go under `data/apps/<Category>/<name>.fap`. Recognised categories:
  Bluetooth, GPIO, Games, Infrared, Media, NFC, RFID, Sub-GHz, Tools, USB, WiFi, iButton.

### `directory.json` (the indexer)

A separate indexer may pre-compute `data/firmware/directory.json` and
`data/apps/directory.json`. When present, the server serves them (re-basing their
`url` fields onto the incoming request host). When absent, the server builds an
equivalent listing live by scanning the data directories — so the API works with or
without the indexer.

If you run the indexer *inside* this container it needs write access to `/srv/data`;
the compose file mounts `data` read-only by default. See the commented note in
`docker-compose.yml` to switch to a read-write mount.

## API

| Method | Path | Description |
| ------ | ---- | ----------- |
| `GET` | `/health` | `{"status":"ok","name":"ZxmWare","version":"..."}` |
| `GET` | `/firmware/directory.json?channel=release\|dev` | Firmware channel index (default `release`) |
| `GET` | `/firmware/{filename}` | Stream a `.tgz` bundle or its `.sha256` sidecar |
| `GET` | `/apps/directory.json` | Catalog of all `.fap` apps |
| `GET` | `/apps/{category}/{filename}` | Stream a single `.fap` |
| `GET` | `/` | Landing page with live counts + installer link |

All responses carry permissive CORS headers so the Web Serial installer page can fetch
them cross-origin. URLs in the directory responses are absolute and built from the
incoming request (honouring `X-Forwarded-Proto` / `X-Forwarded-Host` behind a proxy).

### Example

```bash
curl http://localhost:8000/health
curl "http://localhost:8000/firmware/directory.json?channel=dev"
curl -O http://localhost:8000/firmware/ZxmWare-0053.tgz
curl http://localhost:8000/apps/directory.json
```

## Pointing the companion installer at this server

The companion app and Web Serial installer are configured with a single base URL:

```
BASE_URL=http://<host>:8000
```

Set it to wherever this server is reachable — `http://localhost:8000` for local use, or
your LAN IP / public hostname (e.g. `https://fw.example.com`) otherwise. The installer
derives every other endpoint from it:

- `${BASE_URL}/firmware/directory.json` — to list installable firmware
- `${BASE_URL}/apps/directory.json` — to list installable apps
- the per-bundle / per-app `url` fields returned in those responses — to download

Because the server builds those `url` fields from the request it received, the links it
hands back are always reachable at the same host the installer used.

## Configuration

| Env var | Default | Meaning |
| ------- | ------- | ------- |
| `DATA_DIR` | `<repo>/server/data` (container: `/srv/data`) | Root of the served content tree; must contain `firmware/` and `apps/`. |

## Running without Docker (development)

```bash
cd server
pip install -r app/requirements.txt
DATA_DIR=./data uvicorn app.main:app --reload --port 8000
```
