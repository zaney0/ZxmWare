#!/usr/bin/env bash
#
# build_zxmware.sh - ZxmWare firmware build pipeline.
#
# Wraps the official Flipper Build Tool (`fbt`) to produce a branded ZxmWare OTA
# bundle and pack it into the directory the update server serves from.
#
# TWO WAYS TO GET A BUNDLE
# ------------------------
#   1. FROM SOURCE (this script's main path):
#        - point FW_SRC at a firmware source fork checkout (official / Unleashed /
#          Xtreme / RogueMaster), apply ZxmWare branding, run `fbt`, then rebrand +
#          repack the produced bundle with build/pack_update.py.
#
#   2. REBRAND-ONLY (no source tree / no toolchain):
#        - if you already HAVE a prebuilt OTA bundle directory (one containing
#          update.fuf, firmware.dfu, radio.bin, updater.bin, resources.tar,
#          splash.bin), you do NOT need fbt at all. Skip this script and call
#          pack_update.py directly:
#
#            python3 -I build/pack_update.py rebrand \
#                --src   /path/to/XFW-0053_bundle \
#                --out   server/data/firmware \
#                --name  ZxmWare --version 0053 \
#                --splash build/branding/zxmware_splash.bin
#
#          (You can also run this script with REBRAND_ONLY=1 and BUNDLE_DIR=...
#           to take that shortcut through here.)
#
# IMPORTANT: Neither this script nor pack_update.py ever edits Target / CRC /
# OB-reference / OB-mask / OB-write-mask lines in update.fuf. Those are flashed to
# the device's option bytes; altering them bricks the Flipper. Branding only
# touches the firmware name (Info:) and the boot splash.
#
# USAGE
#   FW_SRC=/path/to/flipperzero-firmware \
#   ZXW_VERSION=0053 \
#     build/build_zxmware.sh
#
# ENV VARS
#   FW_SRC        (required for from-source build) firmware fork checkout root
#   ZXW_NAME      firmware name/brand            (default: ZxmWare)
#   ZXW_VERSION   version suffix, e.g. 0053      (default: dev)
#   ZXW_SPLASH    path to a splash.bin to inject (default: build/branding/zxmware_splash.bin if present)
#   BUILD_APPS    "1" to also run `fbt fap_dist`  (default: 1)
#   REBRAND_ONLY  "1" to skip fbt and just repack BUNDLE_DIR (default: 0)
#   BUNDLE_DIR    prebuilt bundle dir (used when REBRAND_ONLY=1)

set -euo pipefail

# --- resolve paths relative to this script, so it runs from anywhere ----------
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" >/dev/null 2>&1 && pwd)"
REPO_ROOT="$(cd -- "${SCRIPT_DIR}/.." >/dev/null 2>&1 && pwd)"
PACK="${SCRIPT_DIR}/pack_update.py"
BRANDING="${SCRIPT_DIR}/branding/apply_branding.py"
OUT_DIR="${REPO_ROOT}/server/data/firmware"

# --- config (env-overridable) -------------------------------------------------
ZXW_NAME="${ZXW_NAME:-ZxmWare}"
ZXW_VERSION="${ZXW_VERSION:-dev}"
BUILD_APPS="${BUILD_APPS:-1}"
REBRAND_ONLY="${REBRAND_ONLY:-0}"
ZXW_SPLASH="${ZXW_SPLASH:-${SCRIPT_DIR}/branding/zxmware_splash.bin}"

# --- tiny logging helpers -----------------------------------------------------
log()  { printf '\033[36m[*]\033[0m %s\n' "$*"; }
ok()   { printf '\033[32m[+]\033[0m %s\n' "$*"; }
warn() { printf '\033[33m[!]\033[0m %s\n' "$*" >&2; }
die()  { printf '\033[31m[x]\033[0m %s\n' "$*" >&2; exit 1; }

# Build a `--splash <file>` argument for pack_update.py only when the file exists.
splash_arg() {
  if [[ -f "${ZXW_SPLASH}" ]]; then
    printf -- '--splash\n%s\n' "${ZXW_SPLASH}"
  else
    warn "no splash at ${ZXW_SPLASH} - keeping the bundle's existing splash"
  fi
}

# Repack a bundle directory into server/data/firmware/<name>-<ver>.tgz.
repack_bundle() {
  local bundle="$1"
  [[ -d "${bundle}" ]]             || die "bundle dir not found: ${bundle}"
  [[ -f "${bundle}/update.fuf" ]]  || die "no update.fuf in ${bundle} - not an OTA bundle"
  [[ -f "${PACK}" ]]               || die "missing packer: ${PACK}"

  mkdir -p "${OUT_DIR}"
  log "repacking ${bundle} -> ${OUT_DIR}/${ZXW_NAME}-${ZXW_VERSION}.tgz"

  # -I = isolated mode: ignore env/user site + the bundle dir on sys.path, so a
  # planted module inside an untrusted bundle can't be imported by the packer.
  local -a args=(
    python3 -I "${PACK}" rebrand
    --src  "${bundle}"
    --out  "${OUT_DIR}"
    --name "${ZXW_NAME}"
    --version "${ZXW_VERSION}"
  )
  # append --splash <file> only if the splash file exists
  while IFS= read -r line; do [[ -n "${line}" ]] && args+=("${line}"); done < <(splash_arg)

  "${args[@]}"
  ok "packed ${ZXW_NAME}-${ZXW_VERSION}"
}

# ------------------------------------------------------------------------------
# PATH 2: rebrand-only shortcut (no fbt, no source tree)
# ------------------------------------------------------------------------------
if [[ "${REBRAND_ONLY}" == "1" ]]; then
  log "REBRAND_ONLY mode - skipping fbt"
  [[ -n "${BUNDLE_DIR:-}" ]] || die "REBRAND_ONLY=1 requires BUNDLE_DIR=/path/to/bundle"
  repack_bundle "${BUNDLE_DIR}"
  ok "done"
  exit 0
fi

# ------------------------------------------------------------------------------
# PATH 1: full from-source build via fbt
# ------------------------------------------------------------------------------
[[ -n "${FW_SRC:-}" ]] || die "FW_SRC is not set. Point it at a firmware source checkout, \
or use REBRAND_ONLY=1 + BUNDLE_DIR=... to repack a prebuilt bundle."
FW_SRC="$(cd -- "${FW_SRC}" >/dev/null 2>&1 && pwd)" || die "FW_SRC does not exist: ${FW_SRC}"
log "firmware source: ${FW_SRC}"

# fbt lives at the root of a Flipper firmware checkout.
FBT="${FW_SRC}/fbt"
[[ -x "${FBT}" ]] || die "fbt not found or not executable at ${FBT} - is FW_SRC a Flipper firmware checkout?"
ok "found fbt: ${FBT}"

# --- step 1: apply ZxmWare branding to the source tree ------------------------
# This writes fbt.user / DIST_SUFFIX + FIRMWARE_ORIGIN and drops the splash asset
# into the firmware assets path. It is defensive and only touches files that exist.
if [[ -f "${BRANDING}" ]]; then
  log "applying branding (name=${ZXW_NAME} version=${ZXW_VERSION})"
  branding_args=(
    python3 -I "${BRANDING}"
    --fw-src "${FW_SRC}"
    --name   "${ZXW_NAME}"
    --version "${ZXW_VERSION}"
  )
  [[ -f "${ZXW_SPLASH}" ]] && branding_args+=(--splash "${ZXW_SPLASH}")
  "${branding_args[@]}"
  ok "branding applied"
else
  warn "no apply_branding.py at ${BRANDING} - building without rebranding the source"
fi

# --- step 2: build the firmware + updater package via fbt ---------------------
# `fbt updater_package` builds firmware + updater and assembles the OTA bundle
# under dist/f7-*/. Run fbt from inside the source tree (it expects cwd == root).
log "running: ./fbt updater_package  (this compiles firmware; needs the Flipper toolchain)"
(
  cd "${FW_SRC}"
  ./fbt updater_package
)
ok "fbt updater_package complete"

# Optional: build distributable .fap apps as well.
if [[ "${BUILD_APPS}" == "1" ]]; then
  log "running: ./fbt fap_dist  (building distributable apps)"
  (
    cd "${FW_SRC}"
    ./fbt fap_dist
  )
  ok "fbt fap_dist complete"
else
  log "BUILD_APPS=0 - skipping app build"
fi

# --- step 3: locate the produced bundle directory under dist/f7-*/ ------------
# fbt emits the OTA payload into a directory that contains update.fuf. Its exact
# name varies by fork/version, so search for the update.fuf and take its parent.
log "locating produced bundle under ${FW_SRC}/dist/f7-*/"
BUNDLE_DIR=""
# Prefer the newest update.fuf found (most recently built), searching only the f7 dist dirs.
while IFS= read -r fuf; do
  BUNDLE_DIR="$(dirname -- "${fuf}")"
  break
done < <(find "${FW_SRC}/dist"/f7-* -maxdepth 3 -name update.fuf -print 2>/dev/null \
           | xargs -I{} ls -1t "{}" 2>/dev/null)

# Fallback: plain find if the ls-by-time pipe produced nothing.
if [[ -z "${BUNDLE_DIR}" ]]; then
  BUNDLE_DIR="$(dirname -- "$(find "${FW_SRC}/dist"/f7-* -maxdepth 3 -name update.fuf -print 2>/dev/null | head -n1)")" || true
fi

[[ -n "${BUNDLE_DIR}" && -d "${BUNDLE_DIR}" ]] \
  || die "could not find a built bundle (update.fuf) under ${FW_SRC}/dist/f7-*/ - did fbt succeed?"
ok "built bundle: ${BUNDLE_DIR}"

# --- step 4: rebrand + repack into the server's firmware dir ------------------
repack_bundle "${BUNDLE_DIR}"

ok "ZxmWare build complete -> ${OUT_DIR}/${ZXW_NAME}-${ZXW_VERSION}.tgz"
