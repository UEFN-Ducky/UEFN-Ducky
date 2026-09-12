# Remote View direct mode — ship and test runbook

What has to be live for a phone to reach the PC without a tunnel, in the
order it should go out, and how to prove each step. Protocol details are in
`remote-direct-protocol.md`.

## What ships where

| Piece | Repo / artifact | How it gets out |
|---|---|---|
| Desktop app | `UEFN-Ducky-Release` → `dist/UEFN-Ducky-<ver>.exe` | `py build/build_exes.py` (local test) or `py release/publish_app.py` (Store) |
| Phone panel bundle | same repo → `gh-pages` branch → `https://panel.uefnducky.org/<ver>/` | automatic: `py release/publish_app.py` publishes it right after the Store upload. Manual: `py release/publish_panel.py --no-build` after any `build_exes.py` run. |
| Site plugin `uefn-ducky` | `DuckyOS/plugins/plugin-uefn-ducky` → `deploy/uefn-ducky-<ver>.zip` | `bash scripts/release.sh` builds; `bash scripts/release.sh --upload-only` (or the Marketplace admin) installs it on uefnducky.org |
| Panel host DNS | Cloudflare zone `uefnducky.org` | created by the plugin on first `remote-config` call (`panel` CNAME → `uefn-ducky.github.io`, DNS-only) |
| GitHub Pages | repo `UEFN-Ducky/UEFN-Ducky`, branch `gh-pages`, custom domain `panel.uefnducky.org` | **done Sep 12 2026** (org setting `members_can_create_public_pages` turned on, site created from `gh-pages`, CNAME set). GitHub issues the TLS certificate by itself once the DNS CNAME exists; until then the /ducky page falls back to the tunnel after 30 s. |

Order that never breaks a user: **desktop → panel → plugin**. An old plugin
with a new desktop just keeps using the tunnel. A new plugin with an old
desktop gets `method not allowed` from `rtc_connect` and falls back to the
tunnel on its own.

## State as of Sep 12 2026

- Desktop `dist/UEFN-Ducky-1.2.55.exe` built and e2e-verified (direct + tunnel).
- Panel `1.2.55` published to `gh-pages` (`/1.2.55/`, `/latest/`, `/sw.js`, `versions.json`).
- Plugin `uefn-ducky-1.1.93.zip` (Linux build) in `plugins/plugin-uefn-ducky/deploy/`, **not yet uploaded**.
- DNS `panel` CNAME: created automatically by the plugin on the first `/ducky` visit after upload.

## Kill switch and knobs (site, no redeploy)

All through the plugin collect API as an admin session (or the site MCP):

```
POST /api/v1/plugins/uefn-ducky/collect/admin-remote  {"action":"save-direct","direct_enabled":false}
POST /api/v1/plugins/uefn-ducky/collect/admin-remote  {"action":"save-direct","panel_base":"https://panel.uefnducky.org"}
POST /api/v1/plugins/uefn-ducky/collect/admin-remote  {"action":"save-turn","turnKeyId":"<cloudflare realtime turn key id>","turnToken":"<api token>"}
POST /api/v1/plugins/uefn-ducky/collect/admin-remote  {"action":"panel-dns"}
GET-style status:                                     {"action":"status"}  → direct_enabled, panel_base, tunnels, token_set
```

`direct_enabled:false` sends every `/ducky` visit straight to the tunnel
path within one page load. TURN is optional; without it the ladder is
direct → tunnel.

## Test on the website (what you do)

1. Desktop: install/run the new EXE, sign in, Remote access on.
2. Phone: open `https://uefnducky.org/ducky`. Expect the status line
   "Connecting to your PC…" then the panel. The View menu → UEFN shows the
   editor; the stats badge bottom-right reads `H264 · ~60 fps`.
3. Confirm it is direct, not the tunnel: the iframe URL (long-press → open
   in new tab, or desktop devtools) is `panel.uefnducky.org/…`, not
   `u-….uefnducky.org`.
4. Kill-switch drill: flip `direct_enabled:false`, reload `/ducky`, expect the
   `u-….uefnducky.org` iframe and the same working panel. Flip it back.
5. Cellular: repeat 2 on mobile data. If the badge never appears and the
   status says "Direct link unavailable … Using relay…", that phone's NAT
   needs TURN; add a Cloudflare Realtime TURN key (`save-turn`) and retry.

## Test locally (what the machine does)

```
powershell -File tests/e2e/remote_view/run_desktop.ps1      # desktop from source, CDP on 9222
.venv/Scripts/python.exe tests/e2e/remote_view/direct_e2e.py   # headless viewer, PASS/FAIL
```

The driver checks: connect under budget, RPC over the channel, ICE path,
H.264 frames, fit + crop, input, blob allowlist, no viewer exceptions, and
writes `tests/e2e/remote_view/last-viewer.png`.

Unit tests: `pytest ducky_app/frontend`, `npm test` in `web/`,
`cargo test` in `plugins/plugin-uefn-ducky`.

## Telemetry

The desktop's presence heartbeat now carries `remoteDirect`
(`state`, `reason`, `connect_ms`, `candidate`, `attempt`) from the last
viewer session, so the site can chart direct success and candidate type
without any new endpoint.
