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
| `plugin.subscribe` | `{ types: string[] }` | `{ ok, types }` — host push events forwarded to iframe (never `plugin_scope_changed`: the host sends that one itself, with `reason`) |
| `theme.get` | — | `{ vars }` — Appearance CSS vars (keys without `--`) |
| `prefs.get` | optional `{ id }` | `{ prefs }` or `{ id, value }` |
| `prefs.set` | `{ id, value }` (bool/string/number/null) | `{ ok: true }` |
| `scope.get` | — | `{ scope: { kind, label, spaceId, readOnly } }` — whose data this panel shows (`kind`: `personal`, `shared` or `sandbox`) |
| `data.get` | `{ key }` | `{ key, value }` (`null` when missing) |
| `data.put` | `{ key, value }` (any JSON, ≤ 1 MB) | `{ ok, changed }` |
| `data.list` | `{ prefix?, values? }` | `{ keys }`, or `{ items: { key: value } }` with `values: true` |
| `data.delete` | `{ key }` | `{ ok, deleted }` |
| `files.put` | `{ path, b64 }` (≤ 100 MB) | `{ ok, changed, size }` |
| `files.get` | `{ path }` | `{ ok, b64 }` |
| `files.list` | `{ prefix? }` | `{ files: [{ path, size, sha256 }] }` |
| `files.delete` | `{ path }` | `{ ok, deleted }` |
| `live.join` | `{ room? }` | `{ ok, on, room, state, peers, groups }` — go live on the team copy, every plugin with it (no room: a teammate's group, else this PC's own; a group's `room` joins it; any other room is this plugin's own) |
| `live.leave` | — | `{ ok }` — this PC leaves the group, every plugin with it (a room of the plugin's own: only that) |
| `live.state` | — | `{ supported, reason, on, room, state, peers }` |
| `live.send` | `{ topic, data, to? }` (JSON ≤ 16 KB, ≤ 30/s) | `{ ok, sent }` — to teammates live in the room (`to`: one PC's device id); `ducky.*` topics are the host's |
| `chat.open` | `{ key?, title?, brief?, conv_id?, place?: "bottom"\|"right", size?, expanded?, theme? }` | `{ open, expanded, place, size, conv_id, running }` — Ducky's own chat docked in this panel (see below) |
| `chat.set` | `{ expanded?, place?, size?, theme?, open? }` | same as `chat.open` — change the open dock |
| `chat.close` / `chat.state` | — | same — close the dock / read it |

### Docked chat (`chat.*`)

`chat.open` shows Ducky's real chat (messages, tool steps, composer, model picker) inside the
panel, under it (`place: "bottom"`) or beside it (`"right"`). It starts as one slim line (title,
a running dot and timer, expand / open as a tab / close) and expands to `size` px; the person can
drag its edge. The conversation is the plugin's own: one per `key` (remembered, replaced if
deleted), its agent told it lives in the plugin plus your `brief`, or an existing chat via
`conv_id`. Recolor it with `theme: { "--dock-bg": …, "--dock-accent": …, "--fg": … }` (CSS
variables only; values with `url(`, `;` or braces are dropped). Every change comes back as the
event `{ type: "chat.state", open, expanded, place, size, conv_id, running }`.

### Plugin data (host data service)

Store plugin data through `data.*` / `files.*` (panels) or `api.data` (Python
backends) — never a folder you pick yourself. The host keeps it per signed-in
account and per scope: Local, or the one shared space the user picked for this plugin
in Plugins → the plugin → Data (the plugin has no say). Shared copies sync; spaces never
share data with each other. Plugin data on each PC is encrypted for
the signed-in account (docs, files, cache and prefs), so files have no readable
path: read them with `files.get`.

- **One doc per entity** (`card.pip`, `pack.starter`), never one big `db.json`:
  two people editing different cards must not overwrite each other.
- Doc keys `[a-z0-9._-]` ≤ 128; file paths `[a-z0-9._/-]` ≤ 256, no `..`, no
  leading dots.
- A shared scope can be read-only; writes then fail with a clear error. When the
  user loses access to the space, its copy is locked: reads come back empty and
  writes fail until access returns (after a while the copy leaves the PC).
- When a sync brings teammates' changes, the host pushes
  `{ channel, event: { type: "plugin_scope_changed", scope, reason: "synced", keys } }`
  (`keys`: the doc keys and file paths that changed; absent means re-read everything).
  Merge them into what is on screen: keep the open item, selection and scroll. Bursts are
  merged into one push (at most one every ~1 s), and a hidden panel gets one when shown.
- `reason: "switched"`: whose data the panel shows changed (account, Local or a space):
  start over. Switching also restarts the plugin's backend and reloads its open panels on
  the new copy; nothing of the old copy is carried over.
- Items that belong to one PC (diagnostics, per-device state) go under a prefix named in
  `plugin.json` `"data_local": ["qa."]`: they stay in Local whatever copy is shown, never
  sync, and never show as a teammate editing something.

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

### Live editing

On a team copy, the **Live** menu in the title bar starts a group (Start live) or joins
someone's (Join): a group is a room named after its host's PC, and being in it covers
every plugin on that team copy (closing a tab leaves nothing; Leave does). It links this PC straight
to the others in it: peer to peer, through the desktop app's main window, nothing through
a server but the first hello. With no plugin code:

- changes never travel live: a `data.*` / `files.*` write goes out with sync (within a
  second or two while live), and the moment it is committed the group pulls it. Panels
  hear it as the usual `{ type: "plugin_scope_changed", reason: "synced", keys }`, always
  whole (a doc lands with the files it names). Merge `keys`, keep what is open.
- the host script at the top of every panel page sends this pointer and what was
  clicked, and draws teammates' pointers, names and selections over the page.
- pressing a control holds it, live or not: a text field, list or editable while it has
  focus, a button or anything dragged until 1.5 s after the press. Every teammate on the
  same screen sees that control greyed out (`inert`, with the holder's name): the group at
  once, everyone else within seconds through presence. The earlier of two presses keeps
  it. `data-live-lock` makes any element holdable, `data-live-nolock` opts a control out;
  links and tabs never hold.
- **Same screen as the group** in the Live menu: a member's page goes to the host's screen, scrolls with it
  there and clicks the same tabs (`role="tab"`, `a[href^="#"]`, `data-live-nav`); nothing
  else is ever clicked for them. Going there is the plugin's: when the host's
  `data-live-view` isn't this page's, the page gets the window event `ducky-live-goto`
  (`detail.view`: the host's `data-live-view`, several joined with `|`) on joining, on a
  reload and each time the host moves. Open that screen (check it: it came from another
  PC); a page that ignores it stays where it is.
- an `<img>` or `<video>` that fails to load on a team copy shows a placeholder the size
  of the picture ("Getting picture…" while teammates' changes come in) and loads again
  after each sync.

Give elements people point at or press a stable `data-live-id` (else `id`, else their
place in the page) so a pointer or a hold lands on the same thing on every PC, and mark
what a screen shows with `data-live-view` (the open project's id and tab, say) so holds
and pointers never cross from one screen to another: a teammate's pointer only shows
while both pages show the same screen. Without it, a screen is its title, address and
selected tabs.
`live.join({ room })` joins a named room of the plugin's own.

The host also pushes `{ type: "live.state", on }` on load and when live goes on or off,
`{ type: "live.message", from: { name, user, device, label }, topic, data }` for each
`live.send` of a teammate (short-lived things: a playhead, a drag in progress), and
`{ type: "live.peers", on, peers }` when who is live changes (`path`: `direct`,
`forwarded` through a teammate, or `none`: not linked, changes come with sync).
Never rely on `live.message` for data: what matters goes through `data.*`.

Floating (torn-off) windows are separate pages: theme changes, `plugin_scope_changed`
and plugin changes reach panels there too, and a tab moved between windows keeps
one live copy (the window it left releases its panel).

To add a method: one entry in `bridge.ts` → `BRIDGE_HANDLERS`.

## Sandbox rules

- iframe uses `sandbox="allow-scripts allow-pointer-lock"` — **no** `allow-same-origin`.
- Plugin code cannot read app localStorage, cookies, or the host DOM.
- Prefs go through the bridge into the host's `uefn-plugin-ui-prefs` store (scoped by plugin id).
- POSTs to `/__panel_run` / `/__panel_event` / `/__panel_api` from the iframe are rejected (`Origin: null`). Use the `postMessage` bridge instead.
- Sandbox origin is opaque (`null`). Load sibling CSS/JS with `<script src>` / `<link href>` (classic subresources). `fetch()` and ES modules also work on remote: GET `/plugin-ui/` is cookie-exempt and served with `Access-Control-Allow-Origin: *`. Do not `fetch()`-eval your own app.js when a script tag will do. HTML responses send a CSP that blocks Cloudflare’s RUM beacon (`static.cloudflareinsights.com`) so the opaque iframe does not CORS-fail `/cdn-cgi/rum`. Nothing loads from the internet: scripts, styles, fonts and requests are limited to the plugin's own files, so libraries are vendored into `ui/vendor/` (`ducky_plugin_vendor`) and live data goes through the plugin backend.

## Tuning

All knobs live in `constants.ts`.
