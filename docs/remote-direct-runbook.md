# Remote View direct mode — ship and test runbook

What has to be live for a phone to reach the PC without a tunnel, in the order
it ships, and how to prove each step. Protocol details are in
`remote-direct-protocol.md`.

Everything is served by your own tenant. There is no CDN, no second domain, no
GitHub Pages, and no per-user DNS record.

## What ships where

| Piece | Artifact | How it gets out |
|---|---|---|
| Desktop app | `dist/UEFN-Ducky-<ver>.exe` | `py release/publish_app.py` (Store) or `py build/build_exes.py` (local) |
| Phone panel | `plugin-uefn-ducky/assets/panel/` → served at `/static/plugins/uefn-ducky/panel/` | staged by `py release/publish_panel.py`, published by the plugin release |
| Site plugin | `plugin-uefn-ducky/deploy/uefn-ducky-<ver>.zip` | `bash scripts/release.sh --docker --upload` |
| DuckyOS core | the core image | needed **once**, for the plugin-asset and framing change below |

The panel is a build artifact of the desktop repo and is **not committed** to
DuckyOS. `publish_panel.py` copies `web/dist` into the plugin's assets; the
plugin release then packages and uploads it like any other plugin asset. So a
plugin release always has to be preceded by a panel stage.

### Order that never breaks a user

**core → desktop → panel + plugin.** An old plugin with a new desktop keeps
using the tunnel. A new plugin with an old desktop gets `method not allowed`
from `rtc_connect` and falls back to the tunnel on its own. Old core with the
new plugin serves only the panel's JS and CSS, so the panel 404s its wasm and
images and the page falls back to the tunnel — still no user breakage, but the
core change is what makes direct mode actually work.

## The one-time core change

`plugin_assets.rs` used to deliver only `.js .css .svg .map`, which silently
dropped three quarters of a web app. Core now:

- delivers the asset types a bundled app needs (`is_servable_plugin_asset`),
  with correct MIME types, and the sealed manifest walker uses the same gate;
- sends `frame-ancestors 'self'` + `X-Frame-Options: SAMEORIGIN` and a
  `PLUGIN_DOCUMENT_CSP` (wasm, blob workers) for **HTML documents** under
  `/static/plugins/`, so a tenant can embed its own plugin's page.

Subresources and every other path keep the global policy unchanged.

## Deploy

```bash
# 1. desktop → Store (also stages the matching panel into the plugin)
py release/publish_app.py --notes "Remote View: direct mode"

# 2. panel + plugin → tenant
bash plugins/plugin-uefn-ducky/scripts/release.sh --docker --upload
```

`--docker` is not optional: a host build on Windows produces a `plugin.exe`
the site cannot run.

## Kill switch and knobs (site, no redeploy)

As an admin session against the plugin's collect API:

```
{"action":"save-direct","direct_enabled":false}          # everyone back to the tunnel
{"action":"save-direct","panel_base":"/static/plugins/uefn-ducky/panel"}
{"action":"save-turn","turnKeyId":"<id>","turnToken":"<token>"}
{"action":"status"}                                      # direct_enabled, panel_base, tunnels
```

`direct_enabled:false` sends every `/ducky` visit straight to the tunnel within
one page load. TURN is optional; without it the ladder is direct → tunnel.

## Test on the website

1. Desktop: run the new EXE, sign in, Remote access on.
2. Phone: open `https://uefnducky.org/ducky`. Expect "Connecting to your PC…"
   then the panel. View → UEFN shows the editor; the badge reads `H264`.
3. Confirm it is direct: the iframe URL is
   `uefnducky.org/static/plugins/uefn-ducky/panel/index.html`, not `u-….uefnducky.org`.
4. Kill-switch drill: `direct_enabled:false`, reload, expect the `u-…` iframe
   and a working panel. Flip it back.
5. Cellular: repeat 2 on mobile data. If it says "Direct link unavailable …
   Using relay…", that NAT needs TURN — add a key with `save-turn`.

## Test locally

```bash
powershell -File tests/e2e/remote_view/run_desktop.ps1
.venv/Scripts/python.exe tests/e2e/remote_view/direct_e2e.py
```

Checks connect budget, RPC over the channel, ICE path, H.264 frames, fit and
crop, input, blob allowlist, and that the viewer logged no exceptions. Writes
`tests/e2e/remote_view/last-viewer.png`.

That run serves the panel at the origin root, which is how the desktop serves
it. The subdirectory case (direct mode) is covered by
`src/remote/assetBase.test.ts` for URL resolution, and by fetching the panel
from a real tenant once the plugin is installed:

```bash
curl -sI https://<tenant>/static/plugins/uefn-ducky/panel/index.html
curl -sI https://<tenant>/static/plugins/uefn-ducky/panel/assets/<hash>.wasm
```

Expect `200 text/html` with `frame-ancestors 'self'` on the first, and
`200 application/wasm` on the second.

Unit tests: `pytest ducky_app/frontend`, `npm test` in `web/`, `cargo test` in
`plugins/plugin-uefn-ducky` and `duckyos-core`.

## Why the panel lives in a subdirectory now

The panel used to be served from its own origin, where root-relative paths like
`/plugin-ui/x` were fine. Served from a tenant subdirectory those would hit the
site instead, and — worse — sit outside the Service Worker's scope, so nothing
could fetch them from the desktop. Every desktop asset URL now goes through
`assetUrl()` (`src/remote/assetBase.ts`), which prefixes the directory the panel
document was served from. That keeps both modes working and keeps every such URL
inside the worker's scope, so the worker needs no `Service-Worker-Allowed`
header and never claims the site's origin.

## Telemetry

The desktop's presence heartbeat carries `remoteDirect` (`state`, `reason`,
`connect_ms`, `candidate`, `attempt`) from the last viewer session, so direct
success rate and candidate type are visible without a new endpoint.
