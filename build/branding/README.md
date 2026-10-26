# ZxmWare branding & build

Tools for turning a Flipper Zero firmware fork (official / Unleashed / Xtreme /
RogueMaster) into a branded **ZxmWare** OTA bundle that the self-hosted update
server can serve.

## ⚠️ Never edit Target / CRC / OB values

A Flipper OTA manifest (`update.fuf`) contains `Target:` plus CRC and option-byte
lines (`OB-reference`, `OB-mask`, `OB-write-mask`). These are flashed to the MCU's
option bytes. **Changing any of them can brick the device.** Everything in this
folder is cosmetic only — it touches the firmware *name*, *version suffix*, and the
*boot splash*, and nothing else. `pack_update.py` copies those lines through
verbatim.

## Two build modes

### 1. From source (via `fbt`)

Requires a firmware source checkout **and** the Flipper toolchain (`fbt` downloads
it on first run). Driven by `../build_zxmware.sh`:

```bash
FW_SRC=/path/to/flipperzero-firmware \
ZXW_VERSION=0053 \
  ../build_zxmware.sh
```

Pipeline:
1. `apply_branding.py` writes `fbt.user` (`DIST_SUFFIX`, `FIRMWARE_ORIGIN`) and, if
   present, mirrors them into `fbt_options.py`; it also copies the splash into the
   tree's assets path if one exists.
2. `./fbt updater_package` builds firmware + updater and assembles the bundle under
   `dist/f7-*/`. `./fbt fap_dist` additionally builds distributable apps
   (set `BUILD_APPS=0` to skip).
3. The script finds the produced `update.fuf` and calls
   `pack_update.py rebrand` to emit `server/data/firmware/ZxmWare-<ver>.tgz`
   (+ `.sha256`).

### 2. Rebrand-only (no source, no toolchain)

If you already have a prebuilt OTA bundle directory (contains `update.fuf`,
`firmware.dfu`, `radio.bin`, `updater.bin`, `resources.tar`, `splash.bin`), skip
`fbt` entirely:

```bash
python3 -I ../pack_update.py rebrand \
    --src    /path/to/XFW-0053_bundle \
    --out    ../../server/data/firmware \
    --name   ZxmWare --version 0053 \
    --splash zxmware_splash.bin
```

or through the build script:

```bash
REBRAND_ONLY=1 BUNDLE_DIR=/path/to/XFW-0053_bundle ZXW_VERSION=0053 \
  ../build_zxmware.sh
```

This rewrites only the manifest `Info:` line and swaps the splash, then repacks.

## Splash pipeline

The boot splash is a 128x64, 1-bit, heatshrink-compressed image (`hdgr` magic). The
encoder lives inside the firmware tree, so:

- `png_to_splash.py --in logo.png --out splash.bin --fw-src /path/to/fw` shells out
  to the firmware's own asset compiler to do the real encode.
- Without `--fw-src` it only *validates* the PNG (128x64, 1-bit) if Pillow is
  installed — it does **not** hand-roll the binary format.
- A ready `splash.bin` can be injected at pack time with `pack_update.py --splash`,
  which swaps it into the final bundle without rebuilding firmware.

## Files

| File | Purpose |
|------|---------|
| `../build_zxmware.sh` | End-to-end build pipeline (from-source + rebrand-only). |
| `apply_branding.py`   | Sets `DIST_SUFFIX`/`FIRMWARE_ORIGIN`, copies splash into assets. |
| `png_to_splash.py`    | Validates a PNG and encodes it via the firmware's asset tool. |
| `../pack_update.py`   | Rebrands + repacks a bundle into `server/data/firmware/`. |
