# ZxmWare Indexer

Tools that turn a tree of raw files (`server/data/`) into the JSON manifests the
ZxmWare server serves to Flipper Zero updaters and the app catalog.

Both scripts are **stdlib-only** and meant to run under isolated mode:

```sh
python3 -I index_repo.py
python3 -I import_from_bundle.py <source>
```

## Layout

```
server/
  data/
    firmware/
      ZxmWare-0053.tgz
      ZxmWare-0053.tgz.sha256        # optional "<hex>  <name>" sidecar
      ZxmWare-0054-dev.tgz           # "-dev" tag -> dev channel
      directory.json                 # <-- generated
    apps/
      NFC/
        mf_classic_dict.fap
      Sub-GHz/
        subbrute.fap
      directory.json                 # <-- generated
  indexer/
    index_repo.py
    import_from_bundle.py
    README.md
```

## Everyday workflow: re-index after dropping files

1. Drop a new firmware bundle into `server/data/firmware/` as
   `ZxmWare-<ver>.tgz`. Add a `-dev` tag to the version for the dev channel
   (e.g. `ZxmWare-0055-dev.tgz`). A sibling `ZxmWare-<ver>.tgz.sha256` is used
   if present; otherwise the digest is computed.
2. Drop new apps into `server/data/apps/<Category>/<name>.fap`.
3. Re-run the indexer:

   ```sh
   cd server/indexer
   python3 -I index_repo.py --pretty
   ```

The indexer is **idempotent** — run it as often as you like. It rewrites both
`directory.json` files and prints a summary (firmware versions per channel,
app counts per category).

### Useful flags

| Flag | Meaning |
|------|---------|
| `--data-dir PATH` | Scan a different data tree (default: `../data` next to the script, resolved absolute). |
| `--base-url URL` | Prefix every entry `path` with `URL`, e.g. `https://ota.example.com`. Without it, paths stay root-relative like `/firmware/ZxmWare-0053.tgz`. |
| `--pretty` | Indented, sorted JSON (otherwise compact). |
| `--dump-resource-manifest TGZ` | Debug: parse the `resources.tar` `Manifest` inside `TGZ` (absolute, or relative to `--data-dir`) and print it as JSON, then exit. Writes nothing. |

The indexer never extracts bundles to disk — it reads `update.fuf` (for `Info`
and `Target`) straight out of each `.tgz` in memory.

## firmware/directory.json schema

```json
{
  "name": "ZxmWare",
  "generated": 1750000000,
  "channels": {
    "release": [
      {
        "version": "0053",
        "file": "ZxmWare-0053.tgz",
        "path": "/firmware/ZxmWare-0053.tgz",
        "size": 1512983,
        "sha256": "3b1c…e9",
        "target": "7",
        "info": "ZxmWare-0053"
      }
    ],
    "dev": [
      {
        "version": "0054-dev",
        "file": "ZxmWare-0054-dev.tgz",
        "path": "/firmware/ZxmWare-0054-dev.tgz",
        "size": 1519020,
        "sha256": "aa02…17",
        "target": "7",
        "info": "ZxmWare-0054"
      }
    ]
  }
}
```

- `version` comes from the filename (`<Name>-<ver>.tgz`).
- `channel` is `dev` when the filename contains `-dev`, else `release`.
- `target` / `info` are read from the bundle's `update.fuf` (`7` = Flipper Zero
  f7). Either may be `null` if the bundle has no `update.fuf`.
- `sha256` is taken from the `.sha256` sidecar when present, else computed.

## apps/directory.json schema

```json
{
  "name": "ZxmWare Apps",
  "generated": 1750000000,
  "categories": {
    "NFC": [
      {
        "id": "mf_classic_dict",
        "name": "Mf Classic Dict",
        "category": "NFC",
        "file": "mf_classic_dict.fap",
        "path": "/apps/NFC/mf_classic_dict.fap",
        "size": 20480,
        "sha256": "7f9c…c1"
      }
    ],
    "Sub-GHz": [
      {
        "id": "subbrute",
        "name": "Subbrute",
        "category": "Sub-GHz",
        "file": "subbrute.fap",
        "path": "/apps/Sub-GHz/subbrute.fap",
        "size": 33112,
        "sha256": "1d2e…90"
      }
    ]
  }
}
```

- `id` and `name` are derived from the filename; `id` is the canonical key.
- `category` is the parent directory name. Any directory under `apps/` becomes
  a category; the known Flipper categories are Bluetooth, GPIO, Games, Infrared,
  Media, NFC, RFID, Sub-GHz, Tools, USB, WiFi, iButton.
- Downloads use `sha256` (the on-device SD `Manifest` uses md5+size instead; see
  `--dump-resource-manifest`).

## Seeding the app repo from a release: import_from_bundle.py

A firmware release carries its pre-built apps inside `resources.tar` under
`apps/<Category>/*.fap`. To publish those as individually-servable apps, pull
them out of a release you already have:

```sh
cd server/indexer

# From a firmware bundle you already dropped in:
python3 -I import_from_bundle.py ../data/firmware/ZxmWare-0053.tgz

# Preview first (writes nothing):
python3 -I import_from_bundle.py ../data/firmware/ZxmWare-0053.tgz --dry-run

# From a bare resources.tar, or a directory containing either:
python3 -I import_from_bundle.py /path/to/resources.tar
python3 -I import_from_bundle.py /path/to/unpacked_release_dir
```

The importer:

- Accepts a `resources.tar`, a firmware `.tgz` (it finds `resources.tar`
  inside), or a directory containing one of those.
- Copies only safe `apps/<Category>/*.fap` entries into
  `server/data/apps/<Category>/`. Entries with absolute paths, `..`, or that
  escape `apps/` are skipped (path-traversal safe). Nothing is extracted to a
  temp dir.
- Skips files that already exist unless `--force` is given.
- Supports `--apps-dir PATH` to target a non-default destination and `--dry-run`
  to print the plan without writing.

### End-to-end seeding

```sh
cd server/indexer
python3 -I import_from_bundle.py ../data/firmware/ZxmWare-0053.tgz   # seed apps
python3 -I index_repo.py --pretty                                    # catalog them
```

After this, both `firmware/directory.json` and `apps/directory.json` reflect the
current contents of `server/data/`.
