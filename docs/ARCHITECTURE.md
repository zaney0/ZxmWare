# ZxmWare Architecture

ZxmWare is a **self-hosted distribution system** for Flipper Zero firmware and
applications (`.fap`). It builds OTA bundles, serves a signed catalog over HTTP,
and installs from that catalog onto a Flipper via a companion CLI (primary) or a
Web Serial browser GUI (convenience).

This is a maker / embedded-dev tool. Nothing here is a cloud service; you run the
server on your own machine or LAN.

---

## 1. System overview

```
   ┌─────────────────┐     ┌──────────────────────────────┐
   │  Upstream source │     │        BUILD PIPELINE        │
   │  firmware tree,  │────▶│  build/build_zxmware.sh      │
   │  app sources     │     │  build/pack_update.py        │
   └─────────────────┘     │   • compiles / collects .fap │
                            │   • packs OTA bundle (.tgz)  │
                            │   • writes update.fuf        │
                            └──────────────┬───────────────┘
                                           │ artifacts land in
                                           ▼  server/data/
   ┌───────────────────────────────────────────────────────────────┐
   │                           SERVER                                │
   │  server/indexer/   -> scans data/, writes directory.json files  │
   │  server/app/       -> HTTP service (docker compose up)          │
   │                                                                 │
   │  Endpoints:                                                     │
   │    GET /health                                                  │
   │    GET /firmware/directory.json                                 │
   │    GET /firmware/<file>.tgz                                     │
   │    GET /apps/directory.json                                     │
   │    GET /apps/<Category>/<name>.fap                              │
   └───────────────┬───────────────────────────────┬───────────────┘
                   │ HTTP (catalog + artifacts)     │
                   ▼                                 ▼
   ┌──────────────────────────┐       ┌──────────────────────────────┐
   │  COMPANION CLI (primary)  │       │  WEB INSTALLER (convenience)  │
   │  installer/zxmware.py     │       │  installer/web/index.html     │
   │   • sync catalog          │       │   • browse catalog            │
   │   • verify sha256         │       │   • install .fap via WebSerial│
   │   • push over USB serial  │       │   • firmware = experimental   │
   │   • firmware install      │       │   (qFlipper recommended)      │
   └────────────┬─────────────┘       └───────────────┬──────────────┘
                │  USB serial CLI, 115200 baud, prompt ">: "
                └───────────────┬───────────────────────┘
                                ▼
                    ┌───────────────────────┐
                    │     FLIPPER ZERO       │
                    │  Target 7 (stm32wb)    │
                    │  /ext  = microSD root  │
                    │  /ext/apps/<Category>/ │
                    │  /ext/update/ZxmWare/  │
                    └───────────────────────┘
```

**Reliability note.** The companion CLI is the supported installer. The web page
is a convenience GUI focused on *browsing* and installing `.fap` apps. Firmware
flashing over Web Serial is experimental; **qFlipper** is the supported path for
firmware. See `docs/SAFETY.md`.

---

## 2. OTA bundle format

A firmware release is distributed as a gzipped tar (`.tgz`) containing a single
top-level directory named `<Name>-<ver>/` — for example `ZxmWare-0053/`. That
directory is exactly what gets copied to `/ext/update/ZxmWare/` on the SD card,
and it is the standard Flipper OTA update package.

### 2.1 Tgz layout

```
ZxmWare-0053.tgz
└── ZxmWare-0053/
    ├── update.fuf              # update manifest (see below)
    ├── firmware.dfu            # application firmware image (DfuSe)
    ├── core2_firmware.bin      # radio stack (Core2) image, if updated
    ├── resources.tar           # SD-card resource payload (assets, databases)
    └── <optional extras>       # e.g. splash, changelog
```

- `<Name>` is the distribution name (`ZxmWare`).
- `<ver>` is a zero-padded build number (`0053`). The CLI and web page display
  `version` verbatim from `directory.json`.

### 2.2 `update.fuf` keys

`update.fuf` is a small key/value manifest the Flipper's updater reads. It is
produced by `build/pack_update.py`; **never hand-edit it** (especially the CRC
and OB fields — see `docs/SAFETY.md`).

| Key                  | Meaning                                                        |
|----------------------|----------------------------------------------------------------|
| `Filetype`           | `Flipper firmware upgrade configuration`                       |
| `Version`            | manifest format version (integer)                              |
| `Info`               | human-readable release string, e.g. `ZxmWare 0053`             |
| `Target`             | hardware target — **7** for the production stm32wb Flipper     |
| `Loader`             | updater loader image filename                                  |
| `Loader CRC`         | CRC32 of the loader image (computed by the packer)             |
| `Radio`              | Core2 radio stack image filename (optional)                    |
| `Radio address`      | flash address for the radio stack                              |
| `Radio version`      | radio stack version tuple                                      |
| `Radio CRC`          | CRC32 of the radio image                                       |
| `Resources`          | resources payload filename (`resources.tar`)                   |
| `OB reference` / `OB mask` / `OB write mask` | option-byte values — **do not edit** |
| `Firmware`           | application image filename (`firmware.dfu`)                     |
| `Firmware CRC`       | CRC32 of the application image                                 |
| `Splash`             | optional boot splash image filename                            |

`Target 7` is the key compatibility gate: a bundle built for Target 7 installs
only on Target-7 hardware. The packer stamps it; the indexer copies it into the
firmware `directory.json` so clients can filter.

---

## 3. Resources Manifest format (`Manifest`)

The `resources.tar` payload carries a `Manifest` file describing every asset that
belongs on the SD card after an update, so the updater can add/replace/remove
files deterministically. It is a line-oriented text format:

```
V:1
T:1700000000
D:apps
D:apps/Tools
F:2b1e...9ac4:10240:apps/Tools/foo.fap
F:7c0d...11fe:512:subghz/assets/regions.txt
```

| Line | Fields                                  | Meaning                                       |
|------|-----------------------------------------|-----------------------------------------------|
| `V`  | `V:<int>`                               | manifest format **V**ersion                   |
| `T`  | `T:<unix-epoch>`                        | build **T**imestamp                           |
| `D`  | `D:<path>`                              | a **D**irectory that must exist (relative to SD root) |
| `F`  | `F:<sha256>:<size>:<path>`              | a **F**ile: content hash, byte size, dest path |

The updater walks the manifest top-to-bottom: `D` lines create directories, `F`
lines write/verify files (hash + size must match). Anything on the SD card under
a managed path that is *not* in the manifest may be pruned, depending on updater
policy — keep user data outside managed trees.

---

## 4. `directory.json` schemas

The indexer (`server/indexer/`) scans `server/data/` and emits two catalogs. The
server serves them verbatim. These are the exact shapes the CLI and web installer
parse.

### 4.1 `apps/directory.json`

```json
{
  "name": "ZxmWare apps",
  "generated": "2026-10-26T12:00:00Z",
  "categories": {
    "Tools": [
      {
        "id": "foo",
        "name": "Foo",
        "category": "Tools",
        "file": "foo.fap",
        "path": "/apps/Tools/foo.fap",
        "size": 10240,
        "sha256": "2b1e...9ac4"
      }
    ],
    "GPIO": [ "..." ]
  }
}
```

- `categories` maps a **category name** → array of app records.
- Each record: `id`, `name`, `category`, `file`, `path` (server-relative URL),
  `size` (bytes), `sha256` (hex).
- The web page installs each app to `/ext/apps/<category>/<file>` on the Flipper.

### 4.2 `firmware/directory.json`

```json
{
  "name": "ZxmWare firmware",
  "generated": "2026-10-26T12:00:00Z",
  "channels": {
    "release": [
      {
        "version": "0053",
        "file": "ZxmWare-0053.tgz",
        "path": "/firmware/ZxmWare-0053.tgz",
        "size": 8912345,
        "sha256": "9f3c...77b2",
        "target": 7,
        "info": "ZxmWare 0053 — release"
      }
    ],
    "dev": [ "..." ]
  }
}
```

- `channels` maps a **channel name** (`release`, `dev`) → array of build records.
- Each record: `version`, `file`, `path`, `size`, `sha256`, `target` (7 for
  production hardware), `info` (free-text release note/summary).

---

## 5. Data flow: add a new app

```
1. Drop/compile the .fap into the source app tree under its category:
       apps/Tools/foo.fap
2. Import it into the served data dir (hash + size recorded):
       python server/indexer/import_from_bundle.py --apps apps/ --out server/data/apps
   (or copy directly to  server/data/apps/Tools/foo.fap)
3. Re-run the indexer to regenerate the catalog:
       python server/indexer/index.py --data server/data --out server/data
   -> rewrites server/data/apps/directory.json with the new record
4. Server picks it up on next request (or restart). No rebuild needed.
5. Client installs:
       python installer/zxmware.py install-app Tools/foo       # CLI (primary)
   or open installer/web/index.html -> Apps tab -> Install.     # web GUI
```

The record's `sha256` lets the client verify the download before pushing it to
the SD card.

---

## 6. Data flow: cut a new firmware release

```
1. Build / collect the firmware artifacts (application image, radio, resources).
2. Pack the OTA bundle (stamps update.fuf: Target 7, CRCs, OB values):
       python build/pack_update.py \
           --name ZxmWare --version 0053 \
           --fw-root build/out/ZxmWare-0053 \
           --out server/data/firmware/ZxmWare-0053.tgz
   -> produces ZxmWare-0053.tgz containing ZxmWare-0053/ (update.fuf + images)
3. Place it on a channel directory under the served data dir:
       server/data/firmware/release/ZxmWare-0053.tgz   (or .../dev/)
4. Re-run the indexer to regenerate the firmware catalog:
       python server/indexer/index.py --data server/data --out server/data
   -> rewrites server/data/firmware/directory.json with the new channel entry
      (version, size, sha256, target=7, info)
5. Clients see it under the matching channel:
       python installer/zxmware.py install-firmware --channel release 0053  # supported
   or qFlipper for a guaranteed-safe flash.
   The web GUI lists it but keeps firmware behind an experimental confirm().
```

**Why the CLI/qFlipper for firmware:** a firmware flash writes the whole OTA
tree and reboots into the updater. The plain Flipper serial CLI has no binary
framing, so a Web Serial transfer is slow and fragile. The CLI verifies hashes
and qFlipper uses the native USB protocol — both are safer against a mid-flash
interruption. See `docs/SAFETY.md`.
