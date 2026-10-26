#!/usr/bin/env bash
# Install the ZxmWare background agents (macOS launchd):
#   com.zxmware.curator  - builds new apps from your sources daily, notifies you
#   com.zxmware.watcher  - detects the Flipper plugging in, installs queued apps
#
# Usage:  bash tools/automation/install.sh
# Remove: bash tools/automation/uninstall.sh
set -euo pipefail

PROJ="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
PY="$HOME/.zxmware/venv/bin/python"
LOGDIR="$HOME/.zxmware/logs"
LA="$HOME/Library/LaunchAgents"
mkdir -p "$LOGDIR" "$LA"

if [ ! -x "$PY" ]; then
  echo "[!] persistent venv missing at $PY"
  echo "    Create it:  python3 -m venv ~/.zxmware/venv && \\"
  echo "                ~/.zxmware/venv/bin/pip install pyserial 'protobuf>=4,<6' ufbt setuptools && \\"
  echo "                ~/.zxmware/venv/bin/pip install --no-deps flipperzero-protobuf"
  exit 1
fi

write_plist() {  # $1=label $2=plist-body-file-args...
  local label="$1"; shift
  local plist="$LA/$label.plist"
  cat > "$plist"
  echo "$plist"
}

# --- curator: once a day at 09:00, and at load ---
cat > "$LA/com.zxmware.curator.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>com.zxmware.curator</string>
  <key>ProgramArguments</key>
  <array>
    <string>$PY</string>
    <string>$PROJ/tools/curate.py</string>
  </array>
  <key>StartCalendarInterval</key><dict><key>Hour</key><integer>9</integer><key>Minute</key><integer>0</integer></dict>
  <key>RunAtLoad</key><true/>
  <key>StandardOutPath</key><string>$LOGDIR/curator.log</string>
  <key>StandardErrorPath</key><string>$LOGDIR/curator.log</string>
</dict></plist>
PLIST

# --- watcher: poll every 10s for a Flipper plug-in ---
cat > "$LA/com.zxmware.watcher.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>com.zxmware.watcher</string>
  <key>ProgramArguments</key>
  <array>
    <string>$PY</string>
    <string>$PROJ/tools/watch_flipper.py</string>
    <string>--once</string>
  </array>
  <key>StartInterval</key><integer>10</integer>
  <key>RunAtLoad</key><true/>
  <key>StandardOutPath</key><string>$LOGDIR/watcher.log</string>
  <key>StandardErrorPath</key><string>$LOGDIR/watcher.log</string>
</dict></plist>
PLIST

for label in com.zxmware.curator com.zxmware.watcher; do
  launchctl unload "$LA/$label.plist" 2>/dev/null || true
  launchctl load  "$LA/$label.plist"
  echo "[+] loaded $label"
done

echo
echo "ZxmWare agents installed."
echo "  curator: builds new apps from tools/curate_sources.json daily at 09:00 -> notifies you"
echo "  watcher: every 10s, installs queued apps when the Flipper is plugged in"
echo "  logs:    $LOGDIR/{curator,watcher}.log"
echo "  config:  tools/curate_sources.json   (add trusted app repos)"
