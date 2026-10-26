# Firmware-level branding (ZxmWare-0054)

The `ZxmWare-0053` bundle was a *rebrand* of a prebuilt release (bundle name + channel only);
the device's own screens still said "Xtreme". `ZxmWare-0054` is a **from-source build** where the
branding is compiled into the firmware, so the Flipper itself reports ZxmWare.

Verified on-device after flashing:

```
firmware_version      : ZxmWare-0054
firmware_origin_fork  : ZxmWare
firmware_build_date   : 26-10-2026
firmware_target       : 7
```

## Source

- Fork: [Flipper-XFW/Xtreme-Firmware](https://github.com/Flipper-XFW/Xtreme-Firmware) (commit `54619d01`)
- Toolchain: bootstrapped automatically by `fbt` (ARM `arm-none-eabi`, ~1 GB, cached under `toolchain/`)

## Patches applied

| File | Change | Effect |
|------|--------|--------|
| `fbt_options.py` | `FIRMWARE_ORIGIN = "ZxmWare"` | `firmware_origin_fork` + `FW_ORIGIN_ZxmWare` define |
| `fbt_options.py` | `DIST_SUFFIX = "ZxmWare-0054"` | on-device version string (About → Firmware Info); it is `DIST_SUFFIX.split("_")[0]`, so **no underscores** |
| `furi/flipper.c` | `"Xtreme is Booting"` → `"ZxmWare is Booting"` | boot screen |
| `furi/flipper.c` | `"Loading Xtreme Settings"` / `"...Assets"` → `ZxmWare` | boot progress text |
| `applications/system/updater/views/updater_main.c` | `"Xtreme FW"` → `"ZxmWare FW"` | updater screen |

> Only `FW_ORIGIN_Xtreme` depends on the origin name (one spot, in `applications/external/totp/config/app/config.h`,
> and it has a safe `#else` fallback). CRC and option-byte (`OB`) values in `update.fuf` are produced by the build
> and were never hand-edited — editing those bricks the device.

## Build

> **Build from a path with NO spaces.** One external app's asset step (`air_arkanoid`'s
> `sprite_builder.py`) does not quote paths, so a path like `.../AI Cli Projects/...` fails. Build under
> e.g. `~/src/xtreme-src` instead.

```bash
git clone --depth 1 --recurse-submodules --shallow-submodules \
  https://github.com/Flipper-XFW/Xtreme-Firmware.git xtreme-src
cd xtreme-src
# apply the patches in the table above
./fbt updater_package
# -> dist/f7-C/f7-update-ZxmWare-0054/   (update.fuf + firmware.dfu + radio.bin + updater.bin + resources.tar + splash.bin)
```

Then pack/serve it with the ZxmWare tooling and flash with the companion installer or qFlipper.
The on-device install here was done over the Flipper **protobuf-RPC** path (reliable binary transfer),
after confirming the plain text-CLI `storage write` corrupts binary payloads.
