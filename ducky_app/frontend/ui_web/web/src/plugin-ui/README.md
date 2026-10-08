# Plugin webview UI (Phase 2)

Sandboxed HTML panels that ship inside a desktop plugin zip and open as editor tabs.

## How it works

1. Plugin declares `contributes.ui.panels` in `plugin.json` with an `entry` HTML file.
2. Host merges contributions (`backend/uefn_plugins/webview.py`).
3. Loopback server serves files at `/plugin-ui/<pluginId>/<path>`.
4. App opens an editor tab `plugin:<pluginId>:<panelId>` with a sandboxed iframe.
5. Plugin JS talks to the host via `postMessage` (see bridge methods below).

## Authoring a panel

```json
"contributes": {
  "ui.panels": [
    { "id": "game", "title": "My Game", "icon": "duck", "entry": "ui/index.html" }
  ],
  "header.buttons": [
    { "id": "game", "title": "My Game", "icon": "duck", "action": "panel:game", "order": 50 }
  ]
}
```

Put `ui/index.html` (and assets) in the plugin zip. Header action `panel:<panelId>` opens the tab.

## Bridge (from inside the iframe)

```js
const CHANNEL = "uefn-plugin-ui";

function call(method, params = {}) {
  const id = crypto.randomUUID();
  return new Promise((resolve, reject) => {
    function onMsg(ev) {
      const d = ev.data;
      if (!d || d.channel !== CHANNEL || d.id !== id) return;
      window.removeEventListener("message", onMsg);
      if (d.ok) resolve(d.result);
      else reject(new Error(d.error || "bridge error"));
    }
    window.addEventListener("message", onMsg);
    parent.postMessage({ channel: CHANNEL, id, method, params }, "*");
  });
}

await call("plugin.info");
await call("prefs.set", { id: "highScore", value: 42 });
await call("prefs.get", { id: "highScore" });
```

### Methods

| Method | Params | Result |
|--------|--------|--------|
| `plugin.info` | — | `{ pluginId, panelId, version }` |
| `plugin.call` | `{ method, params? }` | result of `api.register_panel_rpc` |
| `plugin.subscribe` | `{ types: string[] }` | `{ ok, types }` — host push events forwarded to iframe |
| `theme.get` | — | `{ vars }` — Appearance CSS vars (keys without `--`) |
| `prefs.get` | optional `{ id }` | `{ prefs }` or `{ id, value }` |
| `prefs.set` | `{ id, value }` (bool/string/number/null) | `{ ok: true }` |
| `scope.get` | — | `{ scope: { kind, label, teamId, readOnly } }` — whose data this panel shows |
| `data.get` | `{ key }` | `{ key, value }` (`null` when missing) |
| `data.put` | `{ key, value }` (any JSON, ≤ 1 MB) | `{ ok, changed }` |
| `data.list` | `{ prefix?, values? }` | `{ keys }`, or `{ items: { key: value } }` with `values: true` |
| `data.delete` | `{ key }` | `{ ok, deleted }` |
| `files.put` | `{ path, b64 }` (≤ 100 MB) | `{ ok, changed, size }` |
| `files.get` | `{ path }` | `{ ok, b64 }` |
| `files.list` | `{ prefix? }` | `{ files: [{ path, size, sha256 }] }` |
| `files.delete` | `{ path }` | `{ ok, deleted }` |

### Plugin data (host data service)

Store plugin data through `data.*` / `files.*` (panels) or `api.data` (Python
backends) — never a folder you pick yourself. The host keeps it per signed-in
account and per scope: Local, or the one team the user picked for this plugin in
Plugins → the plugin → Data (the plugin has no say). Team copies sync; teams never
share data with each other. Plugin data on each PC is encrypted for
the signed-in account (docs, files, cache and prefs), so files have no readable
path: read them with `files.get`.

- **One doc per entity** (`card.pip`, `pack.starter`), never one big `db.json`:
  two people editing different cards must not overwrite each other.
- Doc keys `[a-z0-9._-]` ≤ 128; file paths `[a-z0-9._/-]` ≤ 256, no `..`, no
  leading dots.
- A shared scope can be read-only; writes then fail with a clear error. When the
  user loses access to the team, its copy is locked: reads come back empty and
  writes fail until access returns (a week later the copy leaves the PC).
- When the scope changes or a sync brings new data, the host pushes
  `{ channel, event: { type: "plugin_scope_changed", scope } }`: re-read your docs.
- Switching the plugin between Local and a team restarts its backend and reloads
  its open panels on the new copy; nothing of the old copy is carried over.

The host also pushes `{ channel, event: { type: "appearance_theme", vars } }` on iframe
load and whenever Appearance changes (keys without `--`). Use `var(--bg)`, `var(--fg)`,
`var(--accent)`, `var(--card)`, … — never hardcode colors in plugin HTML, CSS or JS.

### The Ducky UI kit

One tag in a panel page gives it the app's look and the user's theme, live:

```html
<script src="../../_kit/ducky.js"></script>
```

The path is relative to the page (`/plugin-ui/<id>/ui/index.html` → `/plugin-ui/_kit/`;
one more `../` per folder deeper), so it also works where the panel is served from a
subfolder. `ducky.js` links `ducky.css` beside it, applies the Appearance variables
(`theme.get` on load, `appearance_theme` after) and sets `html[data-dk-hidden]` while
the tab is hidden, which pauses the kit's animations. Link `ducky.css` alone if you
apply the theme yourself. Element defaults (`body`, `button`, `input`, `select`, …)
use `:where()`, so your own rules always win.

| Class | What |
|-------|------|
| `dk-btn` + `dk-btn--primary` / `--secondary` / `--ghost` / `--danger` / `--icon` | Buttons |
| `dk-input`, `dk-select`, `dk-field` (+ `__label`, `__hint`) | Inputs and selects |
| `dk-check` (a label around a checkbox or radio) | Checkboxes |
| `dk-tabs` > `dk-tab[aria-selected="true"]` | Tabs |
| `dk-card` (+ `--interactive`, `__title`) | Cards |
| `dk-table` | Tables |
| `dk-badge` + `--accent` / `--success` / `--warn` / `--danger` / `--info` | Badges |
| `dk-empty` (+ `__title`), `dk-loading`, `dk-error` (+ `__title`) | Empty, loading and error states |
| `dk-page`, `dk-stack`, `dk-row`, `dk-muted`, `dk-dim` | Layout and text |

Every control gets a visible `:focus-visible` ring (`var(--border-focus)`). The kit
uses only Appearance variables; `ducky_plugin_validate` holds AI plugins to the same
rule (no color literals, no `var(--x)` the app doesn't set unless the plugin defines it).

### Panel errors

The host puts a small reporter at the top of every panel page: uncaught errors,
rejected promises and `console.error` go over the bridge (`panel.error`) and show up in
`ducky_plugin_errors(id)` with the panel's id, next to backend load errors, panel
crashes, tool exceptions and workflow node errors. At most 20 reports per page load.

### Visibility

A plugin tab stays mounted (hidden) after you switch to another tab, so the panel
keeps its state instead of restarting; it is released when the tab closes or moves
to another window. The host pushes
`{ channel, event: { type: "panel.visibility", visible } }` on every show and hide,
and on each load. Pause heavy work (animation loops, polling, audio, video) while
`visible` is false; the iframe's own `document.visibilityState` does not change when
its tab is hidden.

```js
window.addEventListener("message", (ev) => {
  const e = ev.data?.channel === "uefn-plugin-ui" ? ev.data.event : null;
  if (e?.type === "panel.visibility") e.visible ? resume() : pause();
});
```

Floating (torn-off) windows are separate pages: theme changes, `plugin_scope_changed`
and plugin changes reach panels there too, and a tab moved between windows keeps
one live copy (the window it left releases its panel).

To add a method: one entry in `bridge.ts` → `BRIDGE_HANDLERS`.

## Sandbox rules

- iframe uses `sandbox="allow-scripts allow-pointer-lock"` — **no** `allow-same-origin`.
- Plugin code cannot read app localStorage, cookies, or the host DOM.
- Prefs go through the bridge into the host's `uefn-plugin-ui-prefs` store (scoped by plugin id).
- POSTs to `/__panel_run` / `/__panel_event` / `/__panel_api` from the iframe are rejected (`Origin: null`). Use the `postMessage` bridge instead.
- Sandbox origin is opaque (`null`). Load sibling CSS/JS with `<script src>` / `<link href>` (classic subresources). `fetch()` and ES modules also work on remote: GET `/plugin-ui/` is cookie-exempt and served with `Access-Control-Allow-Origin: *`. Do not `fetch()`-eval your own app.js when a script tag will do. HTML responses send a CSP that blocks Cloudflare’s RUM beacon (`static.cloudflareinsights.com`) so the opaque iframe does not CORS-fail `/cdn-cgi/rum`. Extra script CDNs: unpkg, jsDelivr, cdnjs, esm.sh.

## Tuning

All knobs live in `constants.ts`.
