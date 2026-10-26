# ZxmWare Quickstart

End-to-end: seed a catalog from an existing bundle, index it, run the server,
then install from the CLI and the web GUI. Commands use the real example release
name **`ZxmWare-0053`**.

Prerequisites: Python 3.11+, Docker + Docker Compose, a USB cable, and (for the
web GUI) a Chromium-based browser (Chrome / Edge / Brave / Opera — Web Serial is
not available in Firefox or Safari).

Paths below are relative to the project root (the directory that contains
`build/`, `server/`, `installer/`, and `docs/`).

---

## 1. Seed firmware + apps from an existing bundle

You start from an existing Flipper OTA directory, e.g. `./incoming/ZxmWare-0053/`
(containing `update.fuf`, `firmware.dfu`, `resources.tar`, …).

**(a) Pack the firmware into an OTA `.tgz`.** `pack_update.py` produces the
`<Name>-<ver>/` tgz layout and stamps `update.fuf` (Target 7, CRCs, OB values —
do not edit these by hand):

```bash
python build/pack_update.py \
    --name ZxmWare \
    --version 0053 \
    --fw-root ./incoming/ZxmWare-0053 \
    --out server/data/firmware/release/ZxmWare-0053.tgz
```

> Put it under `release/` for the release channel, or `dev/` for the dev channel.

**(b) Import the apps** from the bundle (or your app tree) into the served data
directory. The importer records each file's size and sha256:

```bash
python server/indexer/import_from_bundle.py \
    --bundle ./incoming/ZxmWare-0053 \
    --out server/data
```

This copies `.fap` files into `server/data/apps/<Category>/` and firmware
artifacts into `server/data/firmware/<channel>/`.

After this step `server/data/` looks like:

```
server/data/
├── apps/
│   ├── Tools/…fap
│   └── GPIO/…fap
└── firmware/
    └── release/
        └── ZxmWare-0053.tgz
```

---

## 2. Run the indexer

Generate both `directory.json` catalogs from what is now in `server/data/`:

```bash
python server/indexer/index.py --data server/data --out server/data
```

This writes:

- `server/data/apps/directory.json`
- `server/data/firmware/directory.json`

Re-run it any time you add or remove an app or firmware build.

Quick sanity check:

```bash
python -c "import json; d=json.load(open('server/data/apps/directory.json')); \
print('categories:', list(d['categories']))"
python -c "import json; d=json.load(open('server/data/firmware/directory.json')); \
print('channels:', list(d['channels']))"
```

---

## 3. Start the server

```bash
docker compose up -d            # from the project root (uses server/)
```

Verify it is serving the catalog:

```bash
curl -s http://localhost:8000/health
curl -s http://localhost:8000/apps/directory.json | head
curl -s http://localhost:8000/firmware/directory.json | head
```

Logs / stop:

```bash
docker compose logs -f
docker compose down
```

---

## 4. Sync and install from the CLI (primary path)

The companion CLI is the supported installer. Point it at the server and sync
the catalog:

```bash
python installer/zxmware.py --server http://localhost:8000 sync
```

List and install apps (verifies sha256, then pushes over the USB serial CLI to
`/ext/apps/<Category>/<name>.fap`):

```bash
python installer/zxmware.py list-apps
python installer/zxmware.py install-app Tools/foo
```

Firmware (supported path — verifies the bundle before flashing):

```bash
python installer/zxmware.py list-firmware
python installer/zxmware.py install-firmware --channel release 0053
```

> For a guaranteed-safe firmware flash, use **qFlipper** instead. Keep the
> Flipper plugged in and charged throughout. See `docs/SAFETY.md`.

---

## 5. Open the web installer (convenience GUI)

The web page is a single self-contained file. Serve it over `http://localhost`
(Web Serial needs a secure context; `localhost` counts).

```bash
# simplest: serve the installer/web directory
python -m http.server 8080 --directory installer/web
```

Then open <http://localhost:8080/> in Chrome/Edge and:

1. **Server** field → `http://localhost:8000` (default; remembered in
   localStorage) → **Connect server**. The page fetches `/health` and both
   `directory.json` files.
2. **Apps tab** → browse by category, filter, click **Install** on an app.
3. **Connect Flipper** → pick the serial port in the browser prompt (115200
   baud). If the button says *Web Serial unavailable*, you are on an
   unsupported browser — use the CLI or qFlipper.
4. Install pushes the `.fap` to `/ext/apps/<Category>/<name>.fap`; watch the log
   console on the right.
5. **Firmware tab** lists the `release`/`dev` channels. Firmware install is
   **experimental** and gated behind a confirmation; the page recommends
   qFlipper and will defer `.tgz` OTA bundles to the CLI/qFlipper.

That's the full loop: **pack → import → index → serve → install**.
