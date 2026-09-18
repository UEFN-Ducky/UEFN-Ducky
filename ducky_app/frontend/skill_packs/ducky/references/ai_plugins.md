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

### Required files

**`plugin.json`** — always set `contributes.agent.tools`. If they asked for a tab:

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
    ]
  },
  "backend": { "entry": "backend", "register": "register" }
}
```

Theme-only plugins use `appearance.*` instead of `ui.panels` — still `@api.tool()`.

**`backend/__init__.py`** — one store, MCP tools + panel RPC:

```python
from __future__ import annotations

def register(api) -> None:
    def _list(kind: str = "") -> dict:
        # load from plugin store / .ducky JSON — return {"ok": True, "items": [...]}
        return {"ok": True, "items": []}

    def _upsert(item: dict) -> dict:
        return {"ok": True, "item": item}

    def _delete(item_id: str) -> dict:
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

    api.register_panel_rpc("list", lambda params=None: _list((params or {}).get("kind") or ""))
    api.register_panel_rpc("upsert", lambda params=None: _upsert((params or {}).get("item") or {}))
    api.register_panel_rpc("delete", lambda params=None: _delete(str((params or {}).get("id") or "")))
    api.log("my_game tools registered")
```

Rename `my_game_*` / intents to the domain. Add get if records are large.

**`ui/index.html`** (only if they asked for a tab) — `plugin.call` those RPCs:

```js
parent.postMessage({ channel: "uefn-plugin-ui", id, method: "plugin.call",
  params: { method: "list", params: { kind: "" } } }, "*");
```

Optional: `skills/<id>/SKILL.md` **inside this plugin** so later chats know the
new tools. Not a separate `ducky_skills_*` pack.

### Contributions (enabled plugin)

| Want | Hook |
|------|------|
| MCP tools for this chat and every IDE | `@api.tool()` in `register(api)` — **required** |
| Sandboxed HTML tab | `ui.panels` + `header.buttons` `panel:<id>` |
| Dock / Settings / header | `dock.panels`, `settings.tabs` / `sections`, `header.buttons` |
| Theme / CSS / FX / skin | `appearance.profiles` / `css` / `effects` / `skin` |
| Verse scaffolds | `verse.templates` |
| Pipeline / automation nodes | `automations.nodes` (+ optional `"systems": ["pipeline"]`) and `@api.register_pipeline_node` / `register_automation_node`. Handler ctx is `{config, payload, node, kind, files, artifact_dir}`. Ship a template that wires `start.chat` → `pipeline.agent` → your node → `pipeline.finish`. |
| Bundled skill | `skills/<id>/SKILL.md` in the draft |

Also: `api.listener`, `api.is_enabled()`, `api.log()`, `api.plugin_id`,
`api.changeset.record`, `api.connection`, `api.register_secret_test`.

Mutating tools: return `_ducky` (or `api.changeset.record`) so Changes → Revert
works. Slot `{plugin_id}://{kind}/{id}/{facet}`.
