# ZxmWare

A **self-hosted custom-firmware distribution + package manager** for the
[Flipper Zero](https://flipperzero.one/). ZxmWare lets you host your own branded
firmware builds and a curated repository of `.fap` applications, then push them to
a Flipper over USB with a companion installer — your own private alternative to the
public firmware/app channels.

> Custom Flipper firmware (Xtreme, Unleashed, RogueMaster, Momentum, official) is a
> normal part of the Flipper ecosystem. ZxmWare is infrastructure for *distributing*
> such a build that **you** produce and host. See [`docs/SAFETY.md`](docs/SAFETY.md)
> — flashing firmware can brick a device if interrupted, and you are responsible for
> complying with local law on what the device transmits.

## What it does

```
          build/                      server/ (Docker)                installer/
   ┌───────────────────┐        ┌──────────────────────┐        ┌──────────────────┐
   │ fbt build / fork  │        │  FastAPI             │        │  zxmware.py (CLI)│
   │ rebrand + splash  │  .tgz  │  /firmware/*.tgz     │  HTTP  │  web/index.html  │
   │ pack_update.py    ├───────►│  /apps/*.fap         ├───────►│  (Web Serial)    │
   └───────────────────┘        │  directory.json x2   │        └────────┬─────────┘
                                 └──────────────────────┘                 │ USB serial
                                                                          ▼
                                                                   🐬 Flipper Zero
```

1. **Build pipeline** (`build/`) — produce an OTA bundle, either by building a
   firmware fork with the official Flipper Build Tool (`fbt`) or by **rebranding an
   existing bundle** (name + splash) with `pack_update.py`. Output is a
   `ZxmWare-<ver>.tgz` in the exact format the Flipper updater installs.
2. **Self-hosted server** (`server/`) — a Dockerized FastAPI app that serves firmware
   bundles, the `.fap` app repo, and two `directory.json` manifests (firmware channels
   + app catalog). An indexer scans your data dir and (re)generates the manifests.
3. **Companion installer/updater** (`installer/`) — a CLI and a Web-Serial browser page
   that talk to the server and flash firmware / install apps onto the Flipper over USB,
   with mandatory `sha256` verification and auto-update.

## Layout

```
ZxmWare/
├── build/
│   ├── pack_update.py          # rebrand/repack an OTA bundle -> ZxmWare-<ver>.tgz   ✅ included
│   ├── build_zxmware.sh        # fbt wrapper (build from a firmware fork)
│   └── branding/               # splash + name branding helpers
├── server/
│   ├── app/main.py             # FastAPI: /firmware, /apps, directory.json
│   ├── indexer/                # index_repo.py + import_from_bundle.py
│   ├── Dockerfile, docker-compose.yml
│   └── data/                   # <-- drop firmware .tgz and .fap files here (git-ignored)
├── installer/
│   ├── zxmware.py              # companion CLI (pyserial + HTTP)
│   └── web/index.html          # Web Serial GUI installer
├── docs/                       # ARCHITECTURE, QUICKSTART, SAFETY
└── Makefile
```

## 60-second start (with a bundle you already have)

```bash
# 1. Rebrand an existing OTA bundle dir (one that contains update.fuf) into ZxmWare
make rebrand SRC=/path/to/XFW-0053_02022024 VERSION=0053

# 2. Seed the app repo from that bundle's resources, then index everything
make seed-apps SRC=/path/to/XFW-0053_02022024
make index

# 3. Serve it
make serve-docker          # or: make serve   (local uvicorn)

# 4. From the companion CLI: sync the catalog and install onto the Flipper
make sync
python3 installer/zxmware.py list-apps
python3 installer/zxmware.py install-app ble_spam
python3 installer/zxmware.py update-firmware --version 0053
```

Full walkthrough: [`docs/QUICKSTART.md`](docs/QUICKSTART.md).
Format + internals: [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).

## OTA bundle format (what ZxmWare produces & installs)

A bundle is a directory packed as `<Name>-<ver>/…` inside a `.tgz`:

| file           | purpose                                        |
|----------------|------------------------------------------------|
| `update.fuf`   | upgrade manifest (`Target: 7`, file refs, CRCs, option bytes) |
| `firmware.dfu` | main MCU firmware (DfuSe)                       |
| `radio.bin`    | BLE copro stack                                |
| `updater.bin`  | self-updater loader                            |
| `resources.tar`| SD resources (`apps/`, `dolphin/`, … + `Manifest`) |
| `splash.bin`   | boot splash (Flipper `hdgr` compressed image)  |

`pack_update.py` only rewrites the `Info:` name and (optionally) swaps the splash —
it **never** touches `Target`, CRC, or option-byte (`OB`) lines, because changing
those bricks the device.
