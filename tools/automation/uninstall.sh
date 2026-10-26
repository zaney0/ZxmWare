#!/usr/bin/env bash
# Remove the ZxmWare background agents.
set -euo pipefail
LA="$HOME/Library/LaunchAgents"
for label in com.zxmware.curator com.zxmware.watcher; do
  launchctl unload "$LA/$label.plist" 2>/dev/null || true
  rm -f "$LA/$label.plist"
  echo "[-] removed $label"
done
echo "ZxmWare agents uninstalled. (venv, SDK and logs under ~/.zxmware are left in place.)"
