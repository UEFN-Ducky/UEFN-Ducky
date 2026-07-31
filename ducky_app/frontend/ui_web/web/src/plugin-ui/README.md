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
| `prefs.get` | optional `{ id }` | `{ prefs }` or `{ id, value }` |
| `prefs.set` | `{ id, value }` (bool/string/number/null) | `{ ok: true }` |

To add a method: one entry in `bridge.ts` → `BRIDGE_HANDLERS`.

## Sandbox rules

- iframe uses `sandbox="allow-scripts allow-pointer-lock"` — **no** `allow-same-origin`.
- Plugin code cannot read app localStorage, cookies, or the host DOM.
- Prefs go through the bridge into the host's `uefn-plugin-ui-prefs` store (scoped by plugin id).
- POSTs to `/__panel_run` / `/__panel_event` from the iframe are rejected (`Origin: null`).

## Tuning

All knobs live in `constants.ts`.
