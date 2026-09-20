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
5. **Tabs: Appearance CSS vars only.** Style with `var(--bg)`, `var(--fg)`,
   `var(--fg-dim)`, `var(--muted)`, `var(--border)`, `var(--card)`,
   `var(--accent)`, `var(--accent-hover)`, `var(--btn-bg)`, `var(--red)`,
   `var(--green)`, `var(--font-family)`, … — apply the host snapshot on load
   (`theme.get`) and on `appearance_theme`. **Never** hardcode `#hex`, `rgb()`,
   or named theme colors. Exception: they specified a design/colors — use those
   tokens only, keep Ducky vars for the rest. **Always mention** the Appearance
   default when you start the UI or when you depart from it.
6. **Always pipeline + automation** (themes too). `contributes.automations.nodes`
   + `@api.register_pipeline_node` (alias of `register_automation_node`) that
   calls the **same** functions as `@api.tool()`. Ship `automations.templates`
   with graph `start.chat` → `pipeline.agent` → your node → `pipeline.finish`.
   Omit `systems` so the tile is on **both** palettes. Theme-only: one node that
   applies/lists the profile. Handler `ctx = {config, payload, node, kind, files,
   artifact_dir}` → `{ok, files?}`.
7. **Bundled skill** `skills/<id>/SKILL.md` inside the draft (not
   `ducky_skills_*`) so later chats know the new tools.
8. **Mutators record changeset** (`_ducky` or `api.changeset.record`, slot
   `{plugin_id}://{kind}/{id}/{facet}`).
9. **Tab UX (if they asked for a tab):** #5 plus `prefs.get` / `prefs.set`;
   empty + error states; `button:focus-visible { outline: 2px solid
   var(--border-focus) }`. Any toggle → `settings.tabs` + `settings.sections`
   (native Ducky settings, not a custom form). First-enable →
   `contributes.walkthrough`.

### Path (no forks)

1. `ducky_plugin_list` — reuse an existing draft/id, or scaffold a new one
2. `ducky_plugin_scaffold(id, label, description)` if needed
3. `ducky_plugin_write_file` until the files below exist
4. `ducky_plugin_validate(id)`
5. `ducky_plugin_install(id)`
6. `ducky_store_set_enabled(id, true)` — if `needs_trust`, **stop** (user confirms
   once). Reinstall reloads; do not send them hunting Store for updates.
7. Iterate: edit the **draft** → validate → install again

Uninstall: `ducky_store_remove(id, confirm=true)`.
Delete draft only: `ducky_plugin_delete_draft(id, confirm=true)`.

Id: `^[a-z][a-z0-9_-]{0,63}$`. Cannot overwrite a Store/local id (uninstall first).
No secrets (`.dat` / `.env` / `.pem` / `.key`).

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
`register_secret_test`, `emit_automation` (automations only — not pipelines),
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
and still a pipeline node that applies/lists the profile.

Toggles (native Settings, not a custom form):

```json
"settings.tabs": [{ "id": "My Game", "label": "My Game", "icon": "duck" }],
"settings.sections": [{
  "tab": "My Game", "id": "general", "title": "General",
  "properties": [{ "id": "enabled", "type": "boolean", "default": true, "label": "Enabled" }]
}]
```

**`backend/__init__.py`** — one store, MCP tools + panel RPC + pipeline node:

```python
from __future__ import annotations

def register(api) -> None:
    def _list(kind: str = "") -> dict:
        return {"ok": True, "items": []}

    def _upsert(item: dict) -> dict:
        api.changeset.record(
            command="upsert", kind="item", ident=str(item.get("id") or ""),
            facet="record", slot=f"{api.plugin_id}://item/{item.get('id') or ''}/record",
        )
        return {"ok": True, "item": item}

    def _delete(item_id: str) -> dict:
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

**`ui/index.html`** (only if they asked for a tab) — Appearance vars + prefs + RPC:

```html
<style>
  body { margin: 0; color: var(--fg); background: var(--bg); font-family: var(--font-family); }
  button { background: var(--btn-bg); color: var(--fg); border: 1px solid var(--border); }
  button.primary { background: var(--accent); color: var(--fg-inverse); }
  button:focus-visible { outline: 2px solid var(--border-focus); outline-offset: 2px; }
  .card { background: var(--card); border: 1px solid var(--border); }
  .muted { color: var(--muted); }
  .error { color: var(--red); border: 1px solid var(--red); }
</style>
<script>
function theme(vars) {
  for (const [k, v] of Object.entries(vars || {})) {
    if (k.startsWith("--") && typeof v === "string")
      document.documentElement.style.setProperty(k, v);
  }
}
window.addEventListener("message", (e) => {
  if (e.data?.event?.type === "appearance_theme") theme(e.data.event.vars);
});
parent.postMessage({ channel: "uefn-plugin-ui", id: "theme", method: "theme.get" }, "*");
parent.postMessage({ channel: "uefn-plugin-ui", id: "prefs", method: "prefs.get" }, "*");
parent.postMessage({ channel: "uefn-plugin-ui", id, method: "plugin.call",
  params: { method: "list", params: { kind: "" } } }, "*");
// empty: show .muted "No items yet."  error: show .error from plugin.call
</script>
```

**`skills/<id>/SKILL.md`** — required inside this plugin so later chats know the
new tools. Not a separate `ducky_skills_*` pack.
