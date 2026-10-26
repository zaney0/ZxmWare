# ZxmWare

A **self-hosted custom-firmware distribution + package manager** for the
[Flipper Zero](https://flipperzero.one/) — host your own branded firmware and a curated
`.fap` app repository, build new apps on request, and push everything to the device over
USB. Your own private firmware/app channel.

> Custom Flipper firmware (Xtreme, Unleashed, Momentum, official) is a normal part of the
> ecosystem. ZxmWare is the infrastructure to *build, brand, host and install* one you
> control. Read [`docs/SAFETY.md`](docs/SAFETY.md) — flashing can brick a device if
> interrupted, and you are responsible for complying with local law on RF transmission.

## Key features

- 🏷️ **Firmware-level branding** — compiles an Xtreme fork so the device itself reports
  ZxmWare (boot screen, About, updater), not just the bundle name.
- 📦 **Self-hosted server** — Dockerized FastAPI serving firmware OTA bundles + a `.fap`
  repo with auto-generated `directory.json` manifests.
- 🔌 **Companion installer** — CLI + Web-Serial GUI that flash firmware and install apps
  over USB, every transfer **md5/sha256-verified**.
- 🛠️ **On-demand app builder** — describe an app, it's compiled to a `.fap` against your
  firmware's SDK and pushed to the device in one command.
- 🤖 **Auto-curator + notify** — a scheduled agent builds new apps from your trusted
  sources and notifies you when there's fresh stuff to install.
- ⚡ **Plug-in auto-sync** — a background watcher installs queued apps the moment you
  plug the Flipper in.
- 🔁 **Reliable RPC transfer** — uses the Flipper protobuf-RPC protocol (what qFlipper
  uses), after proving the text CLI corrupts binary payloads.

## Layout

```
ZxmWare/
├── build/           pack_update.py (rebrand/pack bundles) + fbt wrapper + branding
├── server/          FastAPI app, indexer, Dockerfile; data/ holds bundles + .fap repo
├── installer/       zxmware.py (CLI) + web/index.html (Web Serial GUI)
├── tools/           build_app.py, curate.py, watch_flipper.py + automation/ (launchd)
├── apps_src/        source for on-demand apps (e.g. zxm_hello)
└── docs/            ARCHITECTURE, QUICKSTART, SAFETY, BRANDING, AUTOMATION
```

## Quick start

**Run the server** (hosts firmware + apps):
```bash
cd server && docker compose up -d --build        # or: make serve
```
Open `http://localhost:8000` — firmware builds + app catalog.

**Install onto the Flipper** (plug in over USB):
```bash
pip install -r installer/requirements.txt
export ZXMWARE_SERVER=http://localhost:8000
python3 installer/zxmware.py detect
python3 installer/zxmware.py sync
python3 installer/zxmware.py install-app ble_spam
python3 installer/zxmware.py update-firmware --version 0054
```
Or open `installer/web/index.html` in Chrome/Edge for the Web-Serial GUI.

## Build a custom app on request

```bash
# source lives in apps_src/<name>/ (application.fam + C); then:
python3 tools/build_app.py apps_src/zxm_hello --install
```
Builds the `.fap`, adds it to the catalog, and installs it to the device (md5-verified).
See [`docs/AUTOMATION.md`](docs/AUTOMATION.md).

## Auto-updater (curator + plug-in sync)

```bash
bash tools/automation/install.sh        # installs two launchd agents
```
- **Curator** (daily) builds new apps from `tools/curate_sources.json`, queues them, and
  notifies you to plug in.
- **Watcher** installs the queue automatically when the Flipper connects.

Full setup, config and security notes: [`docs/AUTOMATION.md`](docs/AUTOMATION.md).

## Firmware branding (from source)

`ZxmWare-0053` was a rebrand of a prebuilt bundle; `ZxmWare-0054` is a from-source build
where the device reports `firmware_origin_fork: ZxmWare`. Exact patches + build steps
(incl. the *build from a path without spaces* caveat): [`docs/BRANDING.md`](docs/BRANDING.md).

## Releases

Prebuilt OTA bundles (`.tgz` + sha256) are published under
[Releases](https://github.com/zaney0/ZxmWare/releases): flash with qFlipper or the
companion installer.

## OTA bundle format

A bundle is `<Name>-<ver>/…` in a `.tgz`: `update.fuf` (manifest: Target, file refs,
CRCs, option bytes), `firmware.dfu`, `radio.bin`, `updater.bin`, `resources.tar`,
`splash.bin`. Tooling only ever rewrites the `Info:` name and splash — never CRC/OB
(editing those bricks the device). Details in [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).

## Docs

- [QUICKSTART](docs/QUICKSTART.md) · [ARCHITECTURE](docs/ARCHITECTURE.md) ·
  [AUTOMATION](docs/AUTOMATION.md) · [BRANDING](docs/BRANDING.md) · [SAFETY](docs/SAFETY.md)
