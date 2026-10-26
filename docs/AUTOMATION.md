# ZxmWare automation — on-demand apps, auto-curator, plug-in auto-sync

Three features that make ZxmWare feel alive: ask for an app and get it built, let it
pull new apps on a schedule, and have it install them the moment you plug in.

All of it builds apps with **ufbt against the SDK from your own ZxmWare build**, so every
`.fap` matches the firmware on the device (Target 7). File transfers use the Flipper
**protobuf-RPC** path (reliable binary transfer), never the text CLI.

## One-time setup

```bash
# 1. persistent environment for the background agents
python3 -m venv ~/.zxmware/venv
~/.zxmware/venv/bin/pip install pyserial 'protobuf>=4,<6' ufbt setuptools
~/.zxmware/venv/bin/pip install --no-deps flipperzero-protobuf

# 2. deploy YOUR firmware's SDK into ufbt (matches the device exactly)
~/.zxmware/venv/bin/ufbt update --local path/to/flipper-z-f7-sdk-ZxmWare-0054.zip -t f7

# 3. install the background agents (macOS launchd)
bash tools/automation/install.sh
```

Remove anytime: `bash tools/automation/uninstall.sh`.

## 1. On-demand app builder

Describe what you want; the app is written as a Flipper C app under `apps_src/<name>/`
(an `application.fam` + source), then:

```bash
python3 tools/build_app.py apps_src/<name> --install
```

This builds the `.fap` (in a space-free temp dir), stages it into
`server/data/apps/<Category>/`, re-indexes the catalog, and pushes it to the Flipper
over RPC (md5-verified). Drop `--install` to only add it to the server/catalog.

Example app included: `apps_src/zxm_hello` → **Apps → Tools → ZxmWare Hello**.

> Simple apps build anywhere. Apps with image/sprite assets must build from a
> space-free path — the temp-dir build handles that automatically.

## 2. Auto-curator (`tools/curate.py`)

Runs daily (launchd). For each source in `tools/curate_sources.json` it builds every
app, stages new/updated `.fap`s into the catalog, queues them in
`server/data/pending_install.json`, and sends a macOS notification:
**"N new app(s) ready — plug in your Flipper."**

`tools/curate_sources.json`:
```json
{ "sources": [
  { "type": "local", "path": "apps_src" },
  { "type": "git", "url": "https://github.com/owner/trusted-apps", "ref": "main", "subdir": "applications" }
] }
```

> **Security:** git sources are cloned, compiled, and **run on your device**. Only add
> repositories you trust. Local `apps_src` is the safe default.

Run manually: `~/.zxmware/venv/bin/python tools/curate.py`

## 3. Plug-in auto-sync (`tools/watch_flipper.py`)

Runs every 10s (launchd). When it sees the Flipper connect, it installs everything in
`pending_install.json` over RPC (md5-verified), clears what succeeded, and notifies
**"Installed N app(s) to your Flipper."** So the loop is:

```
curator finds new apps  ->  notifies you  ->  you plug in  ->  watcher installs them  ->  notifies done
```

Run one check manually: `~/.zxmware/venv/bin/python tools/watch_flipper.py --once`

## Where things live

| Path | What |
|------|------|
| `~/.zxmware/venv` | persistent Python env for the agents |
| `~/.ufbt` | ufbt SDK + toolchain (your firmware's SDK) |
| `~/.zxmware/build_tmp` | space-free build scratch |
| `~/.zxmware/curate_cache` | cloned git sources |
| `~/.zxmware/logs` | `curator.log`, `watcher.log` |
| `~/Library/LaunchAgents/com.zxmware.*` | the two agents |
| `server/data/pending_install.json` | install queue (runtime) |

## Limits (honest)

- macOS only (launchd + `osascript` notifications). Linux: run the same scripts under
  systemd timers/udev; notifications via `notify-send`.
- The agents need the Mac awake and the ZxmWare repo present at its path.
- Complex app requests take iteration — Flipper apps are C against the firmware API.
