# Logitech Harmony Hub integration for Unfolded Circle Remote 2/3

A Python reimplementation of the abandoned
[`clarijs/remote2integrationharmony`](https://hub.docker.com/r/clarijs/remote2integrationharmony)
Docker integration (.NET 8, last published 2024-08-17, source never released),
packaged as an **on-device custom integration** rather than an external driver.

## Entity model

Reconstructed from the protocol captures the original author shipped inside the
Docker image (`/app/config/Log*/`), so entity ids stay compatible:

| Entity | Id | Features |
|---|---|---|
| `media_player` (one per hub) | `{hub_id}\|hub\|{hub_id}` | `on_off`, `select_source`, `media_title` + simple command `sync` |
| `remote` (one per device) | `{hub_id}\|device\|{device_id}` | `on_off`, `send_cmd` + generated 4x6 UI pages |

Harmony activities are exposed as the media-player's source list. Device IR
commands become the remote entity's simple commands and UI page buttons.

Two behaviours differ from the original by design:

- **No `media_image_url`.** The original served activity artwork over HTTP; the
  on-device sandbox is a poor place to host an image server.
- **No per-device power state.** The hub reports no feedback for individual
  devices, so a `remote` entity is `ON` whenever its hub is reachable.

## Not yet ported

The original also acted as a **Core-API client**: it authenticated to the Remote
with an API key and auto-created a UC Activity per Harmony activity, complete
with `included_entities`, on/off sequences calling `media_player.select_source`,
uploaded per-activity icons, and a UI profile page grouping them. That is a
separate concern from the integration driver and is not implemented here.

## Integration Manager support

Compatible with [uc-intg-manager](https://github.com/JackJPowell/uc-intg-manager),
which installs and updates custom integrations and backs up their config.

Supporting it requires four things, all of which are in place:

1. **`home_page` in `driver.json` points at the GitHub repo.** The manager
   derives owner/repo from it and compares the latest release tag with the
   installed `version` — so the git tag must match `driver.json`, which CI
   enforces on tagged builds.
2. **Each release carries a `.tar.gz` asset.** With no `asset_pattern` in the
   registry the manager picks the first asset whose name contains `.tar.gz`;
   `build.sh` produces exactly one.
3. **Config backup via the reconfigure flow.** See below.
4. **A registry entry**, submitted as a PR adding `registry-entry.json` to
   [`JackJPowell/uc-intg-list`](https://github.com/JackJPowell/uc-intg-list)'s
   `registry.json`.

### How backup works

The manager performs the backup unattended, with no human answering prompts. It
starts setup with `reconfigure=true`, reads the default value of the dropdown
whose id is `choice`, then replies `{choice, action: "backup", backup_data: "[]"}`
and polls for a page containing a textarea with id `backup_data`.

Those field ids are a wire contract, not internal names — renaming `choice`,
`action`, or `backup_data` disables backups silently, with only a
"may not support backup" line in the manager's log. `tests/test_manager_backup.py`
replays the manager's exact steps using copies of its own parsing helpers, so
such a change fails the suite instead.

This matters most on firmware older than 2.9.3, where the manager updates an
integration by backing up, deleting, and restoring it. Without backup support,
updating would discard your configured hubs.

## Development

```bash
python3 -m venv .venv && . .venv/bin/activate   # Python 3.11+
pip install -r requirements.txt
python3 -m unittest discover -s tests
UC_CONFIG_HOME=./ python3 src/driver.py
```

The driver advertises itself over mDNS; add it from the web-configurator under
*Integrations → Add new → Discover*.

## Build and install

```bash
./build.sh
curl -X POST "http://$REMOTE_IP/api/intg/install?update=true" \
  --user "web-configurator:$PIN" \
  --form "file=@uc-intg-harmony-0.1.0-aarch64.tar.gz"
```

On x86-64 the build needs QEMU binfmt support for the aarch64 image:

```bash
sudo apt install qemu-user-static binfmt-support
```

### Sandbox budget

Custom integrations are throttled at 250 MB RSS and killed at 350 MB, and the
archive may not exceed 100 MB. Measured for v0.1.0:

| | Value | Limit |
|---|---|---|
| Archive | 26 MB | 100 MB |
| Unpacked | 70 MB | — |
| Idle RSS | 71 MB | 250 MB throttle |

The hub connection pins the `WEBSOCKETS` protocol so `slixmpp` is never
imported, and the build excludes it from the bundle. If you re-enable XMPP for
legacy hub firmware, drop `--exclude-module slixmpp` from `build.sh` and
re-measure both numbers.

RSS was sampled idle under QEMU emulation, before any hub connects; expect it to
grow once entities are built from a hub config.
