# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A Logitech Harmony Hub integration driver for the Unfolded Circle Remote 2/3, packaged as an
**on-device custom integration** (a PyInstaller aarch64 binary that runs on the Remote itself),
not as an external network driver. It is a Python reimplementation of the abandoned
`clarijs/remote2integrationharmony` Docker integration; entity ids are deliberately kept
wire-compatible with it so migrating users keep their existing mappings.

## Commands

```bash
# Dev run (Python 3.11+); advertises over mDNS, add via web-configurator → Discover
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
UC_CONFIG_HOME=./ python3 src/driver.py

# Tests
python3 -m unittest discover -s tests
python3 -m unittest tests.test_entities.EntityTest.test_ui_pages_paginate_and_stay_in_bounds

# Build the aarch64 archive (needs Docker; on x86-64 also qemu-user-static + binfmt)
./build.sh

# Install onto a Remote
curl -X POST "http://$REMOTE_IP/api/intg/install?update=true" \
  --user "web-configurator:$PIN" \
  --form "file=@uc-intg-harmony-<version>-aarch64.tar.gz"
```

Useful device endpoints when debugging an install (same basic auth):
`GET /api/system/logs`, `GET /api/intg/drivers?state=true`, `GET /api/intg/instances`.
Note that `/api/intg/instances` returns every integration's `setup_data` unredacted, passwords
included — avoid pasting its raw output around.

## Module layout

`src/` is a **flat set of top-level modules, not a package**. Modules import each other absolutely
(`from hub import Hub`), `src/driver.py` is the PyInstaller entry point, and the tests reach them
via `sys.path.insert(0, "../src")`. Converting this to a package would break all three at once.

| Module | Role |
|---|---|
| `driver.py` | Entry point; owns the `ucapi.IntegrationAPI`, the `hubs` registry, and the event wiring |
| `hub.py` | Wraps `aioharmony`; exposes a hub as activities + devices and emits `pyee` events |
| `setup_flow.py` | Driver setup, reconfiguration, and the uc-intg-manager backup/restore contract |
| `config.py` | Hub list persisted to `$UC_CONFIG_HOME/config.json` |
| `media_player.py` / `remote.py` | Entity construction, attribute mapping, command handlers |
| `const.py` | Entity-id encode/decode helpers |

## Architecture

**Event flow.** `Hub` emits `CONNECTED` / `DISCONNECTED` / `ACTIVITY_CHANGED` / `CONFIG_UPDATED`
through an `AsyncIOEventEmitter`. `driver.py` binds those to two handlers: `_on_hub_available`
rebuilds and republishes the entity set, `_on_hub_state` pushes fresh attributes to whichever
configured entities belong to that hub. Entities are **rebuilt, not mutated** — `_publish_entities`
removes each id before re-adding it, because `available_entities.add()` silently ignores an existing
id and would otherwise pin a stale hub config.

Note the ordering hazard in `add_hub`: a hub configured through the setup flow is already connected
by the time it is registered, so its `CONNECTED` event fired before the listeners existed. `add_hub`
compensates by calling `_on_hub_available` directly when `hub.connected`.

**Entity ids are a wire format**: `{hub_id}|{kind}|{target_id}`, where kind is `hub` or `device`
(`const.py`). One `media_player` per hub (activities become its source list) and one `remote` per
device (IR commands become simple commands plus generated 4x6 UI pages). The shape is inherited from
the original Docker integration and is covered by tests — do not change it casually.

**ucapi command handlers must declare a parameter literally named `websocket`.** ucapi inspects the
handler signature for that name to decide how to invoke it; renaming it breaks command dispatch at
runtime with no static warning.

## Deployment constraints (these have bitten before)

**Only `./bin`, `./config` and `./data` of the installed archive are readable at runtime.** The
archive's root `driver.json` exists for the installer, which reads it before unpacking — the running
driver cannot open it. `build.sh` therefore passes `--add-data driver.json:.` and `driver.py` loads
it from `sys._MEIPASS`. Reverting to a root-relative path makes the driver exit at startup, which
surfaces only as endless `Connection refused` in the core log.

**The Remote assigns the WebSocket port** via `UC_INTEGRATION_HTTP_PORT`. `driver.json` deliberately
has no `port` field; the range 8000-9200 and port 13333 are reserved on the device.

**`slixmpp` is excluded from the bundle** to stay inside the memory budget. This is safe only because
`hub.py` pins `protocol=WEBSOCKETS` and `aioharmony` imports its XMPP connector lazily, inside
`_websocket_or_xmpp`. If you ever enable the XMPP fallback for legacy hub firmware, drop
`--exclude-module slixmpp` from `build.sh` and re-measure the RSS figures in the README.

## Release coupling

Three things must move together, and CI enforces the first: the git tag (`v<x.y.z>`) must equal
`driver.json`'s `version`, and each release must carry exactly one `.tar.gz` asset. `uc-intg-manager`
derives owner/repo from `home_page` and compares the latest release tag against the installed
version, so a mismatch shows a permanent phantom "update available" to every user. Bumping
`driver.json` is a mandatory part of cutting a release, not an afterthought.

## uc-intg-manager backup contract

Reconfiguration doubles as the backup/restore interface, driven unattended with no human answering
prompts. The manager starts setup with `reconfigure=true`, reads the default value of the dropdown
whose id is `choice`, replies `{choice, action: "backup", backup_data: "[]"}`, and polls for a page
carrying a textarea with id `backup_data`. Those three field ids are a wire contract with an external
tool, not internal names — renaming one disables backups silently. `tests/test_manager_backup.py`
replays the manager's exact steps using copies of its own parsing helpers so such a change fails the
suite instead.
