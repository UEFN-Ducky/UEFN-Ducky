---
description: "Build AI-made desktop plugins — tabs, panels, MCP tools. ducky_plugin_* only."
metadata:
  order: 3
  label: "AI-made plugins"
  default_enabled: true
  load_condition: "User asks to create/customize a desktop plugin, theme, skin, tab, panel, or add app functionality via plugins"
---

## AI-made desktop plugins

Author a plugin **for this install** with `ducky_plugin_*` only. Drafts are shared
across chats. Tools write the files; you never open those folders.

**HARD — do this or you failed**

1. `ducky_plugin_list` is the census (`drafts` + `installed`). Never Glob, shell,
   PowerShell, IDE-Read, or `workspace_write_file` into AppData `ai_plugins` /
   `uefn_plugins`. Empty drafts and no matching installed id → scaffold.
2. Edit **only** with `ducky_plugin_write_file` / `read_file` / `list(id=…)`.
3. Every plugin **must** `@api.tool()` every user-facing action (list / get /
   create / update / delete — not a lone ping). Panel RPC calls the **same**
   functions. A tab without matching MCP tools is incomplete.
4. Never git-clone `uefn-plugin-*`, never `publish_plugin.sh`, never
   Install-from-file, never `cp` into AppData, never edit `ducky_app/` / the EXE
   to add a tab. That Store-repo path is for humans shipping a git plugin — not
   chat. Never `ducky_skills_create_pack` unless they asked for a **skill pack**.
5. **Tabs: the Ducky UI kit + Appearance CSS vars only.** One tag in each panel
   page, `<script src="../../_kit/ducky.js"></script>` (one more `../` per folder
   deeper than `ui/`): it links `ducky.css` (`dk-btn`, `dk-btn--primary`,
   `dk-input`, `dk-tabs`, `dk-card`, `dk-table`, `dk-badge`, `dk-empty`,
   `dk-loading`, `dk-error`, focus ring) and applies the user's theme live. Your
   own CSS uses `var(--bg)`, `var(--fg)`, `var(--fg-dim)`, `var(--muted)`,
   `var(--border)`, `var(--card)`, `var(--accent)`, `var(--accent-hover)`,
   `var(--btn-bg)`, `var(--red)`, `var(--green)`, `var(--font-ui)`, … — only
   variables the app sets (or ones you define). **Never** `#hex`, `rgb()`,
   `hsl()` or named colors: `ducky_plugin_validate` rejects them. If they specified
   colors, ship those as an Appearance theme (`appearance.css` /
   `appearance.profiles`) and keep the panel on the variables. **Always mention**
   the Appearance default when you start the UI or when you depart from it.
6. **Always a workflow node** (themes too). `contributes.automations.nodes`
   + `@api.register_pipeline_node` (alias of `register_automation_node`) that
   calls the **same** functions as `@api.tool()`. Ship `automations.templates`
   with graph `start.chat` → `pipeline.agent` → your node → `pipeline.finish`.
   Workflows that call each other ship as one folder template (`"kind": "bundle"`,
   `root`, `folders`, `workflows` with `key`s; a Run workflow step names one as
   `"@key"`; see the workflows reference).
   Every workflow has one palette (`systems` is ignored). Theme-only: one node that
   applies/lists the profile. Handler `ctx = {config, payload, node, inputs, kind,
   files, artifact_dir}` → `{ok, files?}` (`kind` is `"pipeline"` when run from a chat;
   `inputs` holds the node's declared input pins: what is wired in, else set in its
   details). A config field `{"type": "backend", "node_type": "image.generate"}`
   shows the same backend picker and gateway settings as that built-in node; read the
   pick from `config[<field id>]` and its settings from `config.gateway_config`.
   Place graphs with `save_workflow` — each save opens the Workflows editor and
   refreshes the canvas. Delete with `delete_workflow`. Reusable starters:
   `save_workflow_template` / `delete_workflow_template`. Do not tell the user
   to open the tab.
7. **Bundled skill** `skills/<id>/SKILL.md` inside the draft (not
   `ducky_skills_*`) so later chats know the new tools.
8. **Mutators record changeset** (`_ducky` or `api.changeset.record`, slot
   `{plugin_id}://{kind}/{id}/{facet}`).
9. **Tab UX (if they asked for a tab):** #5 plus `prefs.get` / `prefs.set`;
   empty + loading + error states (`dk-empty`, `dk-loading`, `dk-error`); a
   visible focus (the kit's, or `button:focus-visible { outline: 2px solid
   var(--border-focus) }`). Pause heavy work while hidden (`panel.visibility`).
   Any toggle → `settings.tabs` + `settings.sections` (native Ducky settings, not
   a custom form). First-enable → `contributes.walkthrough`.
10. **Never read `.py` files** (your own source, `inspect.getsource`, `open(…
   ".py")`, `runpy` / `spec_from_file_location`): installed plugins can be
   compiled and the files are gone. Import modules; keep data in `.json`.

### Path (no forks)

1. `ducky_plugin_list` — reuse an existing draft/id, or scaffold a new one
2. `ducky_plugin_scaffold(id, label, description)` if needed
3. `ducky_plugin_write_file` until the files below exist
4. `ducky_plugin_validate(id)` — every error says how to fix it
5. `ducky_plugin_install(id)`
6. `ducky_store_set_enabled(id, true)` — if `needs_trust`, **stop** (user confirms
   once). Reinstall reloads; do not send them hunting Store for updates.
7. `ducky_plugin_test(id)` — reinstalls, calls each tool with sample input, runs
   each node (in a throwaway data scope), opens each panel, checks the UI files.
   `ducky_plugin_errors(id, since)` lists load errors, panel errors and crashes,
   tool exceptions and node errors.
8. Iterate: edit the **draft** → validate → test again

Uninstall: `ducky_store_remove(id, confirm=true)`.
Delete draft only: `ducky_plugin_delete_draft(id, confirm=true)`.

Id: `^[a-z][a-z0-9_-]{0,63}$`. Cannot overwrite a Store/local id (uninstall first).
No secrets (`.dat` / `.env` / `.pem` / `.key`).

### Spotlight another program

`api.spotlight(window="uefn", box={"x", "y", "w", "h"}, title=, body=, click=, wait=)`
highlights a control in UEFN, Blender, or any window. Same call as `ducky_ui_show`.
`box` is fractions of the window (0 to 1). The desktop dims and only that hole can
be clicked. Workflows can listen for `spotlight.step` and `spotlight.closed`, or use
the built-in **Spotlight** node (`ui.spotlight`) — you do not register that node.

### Plug into Ducky

**Always** (domain or theme): `agent.tools` + `@api.tool()` · automations
nodes/template · bundled skill · changeset on writes · CSS vars if HTML.

**When the domain needs it** (do not skip if it applies):

| Need | Hook |
|------|------|
| Prefs / secrets | `settings.sections` (`boolean` / `string` / `select` / `secret` + `api.register_secret_test`) |
| Header Connections row | `api.connection(fn)` cheap probe |
| Verse scaffolds | `verse.templates` |
| Sounds | `sounds` + `hooks` + `ducky:hook` (not automations) |
| Dock / file editor | `dock.panels` / `editor.kinds` `ui: "panel:…"` |
| Theme / skin / FX | `appearance.profiles` / `css` / `effects` / `skin` — never set `:root` from boot scripts |

**Only if they asked:** `llm.providers`, `llm.coding_agents`, `ide.hookups`,
`tts.voices`, `shell.boot`. Gateway recipes live in the desktop-plugins
`reference.md` — do not invent a provider.

`api` helpers: `listener`, `http_json`, `poll`, `changeset`, `connection`,
`register_secret_test`, `emit_automation` (runs the workflows on this PC whose trigger matches),
`is_enabled()`, `log()`, `plugin_id`.

### Required files

**`plugin.json`** — always `agent.tools` + `automations`. If they asked for a tab:

```json
{
  "id": "my_game",
  "kind": "plugin",
  "version": "1.0.0",
  "label": "My Game",
  "description": "Manage game data",
  "min_app_version": "1.0.0",
  "default_enabled": false,
  "secret_keys": [],
  "contributes": {
    "agent.tools": {
      "category": "my_game",
      "intent_pattern": "\\b(my_game|my game)\\b"
    },
    "ui.panels": [
      { "id": "main", "title": "My Game", "icon": "duck", "entry": "ui/index.html" }
    ],
    "header.buttons": [
      { "id": "main", "title": "My Game", "icon": "duck", "action": "panel:main", "order": 50 }
    ],
    "automations": {
      "nodes": [
        { "id": "my_game.run", "label": "My Game", "group": "My Game",
          "config_fields": [{ "id": "kind", "label": "Kind", "type": "string" }] }
      ],
      "templates": [
        {
          "id": "my-game-run",
          "label": "Run My Game",
          "description": "Chat start → Agent → My Game → Finish.",
          "graph": {
            "nodes": [
              { "id": "s", "type": "start.chat", "x": 0, "y": 0, "config": {} },
              { "id": "a", "type": "pipeline.agent", "x": 160, "y": 0, "config": {} },
              { "id": "n", "type": "my_game.run", "x": 320, "y": 0, "config": {} },
              { "id": "f", "type": "pipeline.finish", "x": 480, "y": 0, "config": {} }
            ],
            "edges": [
              { "source": "s", "target": "a", "kind": "main" },
              { "source": "a", "target": "n", "kind": "main" },
              { "source": "n", "target": "f", "kind": "main" }
            ]
          }
        }
      ]
    },
    "walkthrough": {
      "id": "my_game",
      "title": "My Game",
      "auto_start": "first_enable",
      "steps": [
        { "target": "header.button.main", "title": "Open My Game",
          "body": "This tab lists and edits records.", "advance": "next" }
      ]
    }
  },
  "backend": { "entry": "backend", "register": "register" }
}
```

Theme-only plugins use `appearance.*` instead of `ui.panels` — still `@api.tool()`
and still a workflow node that applies/lists the profile.

Toggles (native Settings, not a custom form):

```json
"settings.tabs": [{ "id": "My Game", "label": "My Game", "icon": "duck" }],
"settings.sections": [{
  "tab": "My Game", "id": "general", "title": "General",
  "properties": [{ "id": "enabled", "type": "boolean", "default": true, "label": "Enabled" }]
}]
```

**`backend/__init__.py`** — one store, MCP tools + panel RPC + workflow node.

Data lives in `api.data`, never in a folder you pick (no `%LOCALAPPDATA%` paths,
no `db.json`): **one JSON doc per record** (`data.put("item.<id>", {...})`,
`data.get`, `data.items("item.")`, `data.delete`) and files through
`data.put_file(path, bytes)` / `data.get_file(path)` / `data.has_file(path)` /
`data.delete_file` (files are encrypted on disk, so there is no path to read).
Keys are `[a-z0-9._-]`, file paths `[a-z0-9._/-]`. The host keeps the data per
account and scope, encrypted for the signed-in account. The user picks per plugin
whether it stays Local or is shared with one team (Plugins → the plugin → Data);
the plugin never decides and needs no code for it. A scope can be read-only, so
let write errors surface as tool errors.

```python
from __future__ import annotations

def register(api) -> None:
    data = api.data

    def _list(kind: str = "") -> dict:
        rows = data.items("item.").values()
        return {"ok": True, "items": [r for r in rows if not kind or r.get("kind") == kind]}

    def _upsert(item: dict) -> dict:
        item_id = str(item.get("id") or "")
        data.put(f"item.{item_id}", item)
        api.changeset.record(
            command="upsert", kind="item", ident=item_id,
            facet="record", slot=f"{api.plugin_id}://item/{item_id}/record",
        )
        return {"ok": True, "item": item}

    def _delete(item_id: str) -> dict:
        data.delete(f"item.{item_id}")
        api.changeset.record(
            command="delete", kind="item", ident=item_id, facet="record",
            slot=f"{api.plugin_id}://item/{item_id}/record",
        )
        return {"ok": True, "id": item_id}

    @api.tool(intent=r"\b(my_game|cards?)\b")
    def my_game_list(kind: str = "") -> dict:
        """List records this plugin manages."""
        return _list(kind)

    @api.tool(intent=r"\b(my_game|cards?)\b")
    def my_game_upsert(item: dict) -> dict:
        """Create or update one record."""
        return _upsert(item)

    @api.tool(intent=r"\b(my_game|cards?)\b")
    def my_game_delete(item_id: str) -> dict:
        """Delete one record by id."""
        return _delete(item_id)

    @api.register_pipeline_node("my_game.run")
    def my_game_run(ctx: dict) -> dict:
        cfg = ctx.get("config") or {}
        return _list(str(cfg.get("kind") or ""))

    api.register_panel_rpc("list", lambda params=None: _list((params or {}).get("kind") or ""))
    api.register_panel_rpc("upsert", lambda params=None: _upsert((params or {}).get("item") or {}))
    api.register_panel_rpc("delete", lambda params=None: _delete(str((params or {}).get("id") or "")))
    api.log("my_game tools registered")
```

Rename `my_game_*` / intents to the domain. Add get if records are large.

**`ui/index.html`** (only if they asked for a tab) — the UI kit + prefs + RPC:

```html
<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <!-- The Ducky UI kit: ducky.css + the user's theme, live. -->
  <script src="../../_kit/ducky.js"></script>
  <style>
    .list { display: grid; gap: 8px; }
  </style>
</head>
<body class="dk-page">
  <div class="dk-row">
    <button class="dk-btn dk-btn--primary" id="add">Add</button>
    <button class="dk-btn dk-btn--ghost" id="refresh">Refresh</button>
  </div>
  <div id="view" class="list"><div class="dk-loading">Loading…</div></div>
  <script>
    const CHANNEL = "uefn-plugin-ui";
    function call(method, params = {}) {
      const id = crypto.randomUUID();
      return new Promise((resolve, reject) => {
        function onMsg(ev) {
          const d = ev.data;
          if (!d || d.channel !== CHANNEL || d.id !== id) return;
          window.removeEventListener("message", onMsg);
          d.ok ? resolve(d.result) : reject(new Error(d.error || "bridge error"));
        }
        window.addEventListener("message", onMsg);
        parent.postMessage({ channel: CHANNEL, id, method, params }, "*");
      });
    }
    async function load() {
      const view = document.getElementById("view");
      try {
        const out = await call("plugin.call", { method: "list", params: { kind: "" } });
        view.innerHTML = out.items?.length
          ? out.items.map((i) => `<div class="dk-card">${i.id}</div>`).join("")
          : '<div class="dk-empty"><div class="dk-empty__title">No items yet</div></div>';
      } catch (err) {
        view.innerHTML = `<div class="dk-error">${err.message}</div>`;
      }
    }
    document.getElementById("refresh").onclick = load;
    load();
  </script>
</body>
</html>
```

Theme vars by hand instead of the kit: `theme.get` / `appearance_theme` give
`{ vars }` with keys **without** `--`; set each with
`document.documentElement.style.setProperty("--" + key, value)`.

**`skills/<id>/SKILL.md`** — required inside this plugin so later chats know the
new tools. Not a separate `ducky_skills_*` pack.
