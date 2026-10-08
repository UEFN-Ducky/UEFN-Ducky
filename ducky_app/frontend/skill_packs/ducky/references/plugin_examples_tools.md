---
description: "Worked plugin examples without much UI: tools only, workflow nodes with pins, triggers and folder templates, settings and secrets, connection checks, team-shared data"
metadata:
  order: 5
  label: "Plugin examples: tools, nodes, settings, data"
  default_enabled: false
  load_condition: "Building a plugin that adds MCP tools, workflow nodes or templates, settings or API keys, a Connections row, or data shared with a team"
---

# Plugin examples: tools, nodes, settings, data

Each example shows the parts that matter for its case. Every plugin still needs
`agent.tools`, a tool per action, a workflow node plus a template that uses it, and
`skills/<id>/SKILL.md` (`ai_plugins`).

## 1. Tools only (blend with Ducky's tools)

No panel: the chat and workflows are the UI. First `ducky_find_tools("asset")` to see
what exists, then build on it with `api.call_tool` instead of re-implementing. This
plugin audits textures with the app's own `search_assets` tool and keeps each report.

```json
"contributes": {
  "agent.tools": { "category": "asset_audit", "intent_pattern": "\\b(asset audit|audit (my )?assets)\\b",
                   "plan_tools": ["asset_audit_reports"] },
  "automations": {
    "nodes": [{ "id": "asset_audit.run", "label": "Audit assets", "group": "Asset Audit",
                "config_fields": [{ "id": "folder", "label": "Folder", "type": "string" }],
                "outputs": [{ "id": "report", "label": "Report", "type": "json" }, { "id": "text", "label": "Summary", "type": "text" }] }],
    "templates": [{ "id": "asset-audit-run", "label": "Audit my assets", "graph": { "nodes": [
      { "id": "s", "type": "start.chat", "x": 0, "y": 0, "config": {} },
      { "id": "a", "type": "pipeline.agent", "x": 300, "y": 0, "config": {} },
      { "id": "n", "type": "asset_audit.run", "x": 600, "y": 0, "config": { "folder": "/Game/" } },
      { "id": "f", "type": "pipeline.finish", "x": 900, "y": 0, "config": {} }],
      "edges": [{ "source": "s", "target": "a", "kind": "main" }, { "source": "a", "target": "n", "kind": "main" },
                { "source": "n", "target": "f", "kind": "main" }] } }]
  }
}
```

```python
from __future__ import annotations

import time


def register(api) -> None:
    data = api.data

    def run_audit(folder: str = "/Game/") -> dict:
        try:
            found = api.call_tool("search_assets", {"class_name": "Texture2D", "directory": folder or "/Game/", "limit": 500})
        except ValueError as exc:
            return {"ok": False, "error": f"Couldn't list assets: {exc}"}
        assets = found.get("assets") or [] if isinstance(found, dict) else []
        report = {"id": str(int(time.time())), "folder": folder, "textures": len(assets), "at": time.time()}
        try:
            data.put(f"report.{report['id']}", report)
        except PermissionError as exc:
            return {"ok": False, "error": str(exc)}
        text = f"{len(assets)} textures in {folder}."
        return {"ok": True, "report": report, "text": text}

    # Calls a UEFN tool, so it keeps the listener gate (no listener=False).
    @api.tool(intent=r"\b(asset audit|audit (my )?assets)\b")
    def asset_audit_run(folder: str = "/Game/") -> dict:
        """Count the textures under a content folder and save the report."""
        return run_audit(folder)

    @api.tool(intent=r"\b(asset audit|audit (my )?assets)\b", listener=False)
    def asset_audit_reports() -> dict:
        """List saved audit reports, newest first."""
        rows = sorted(data.items("report.").values(), key=lambda r: r.get("at", 0), reverse=True)
        return {"ok": True, "reports": rows}

    @api.register_pipeline_node("asset_audit.run")
    def node_run(ctx: dict) -> dict:
        return run_audit(str((ctx.get("config") or {}).get("folder") or "/Game/"))
```

## 2. Workflow nodes, pins, triggers and templates

Node rows in `contributes.automations.nodes`:

| Field | What |
|---|---|
| `id` | `<plugin_id>.<name>`; the handler registers the same id |
| `label`, `group`, `description`, `icon` | How it shows in the node picker |
| `config_fields` | Settings in the details panel: `{id, label, type}`; types `string`, `number`, `boolean`, `select` (with `options`), `backend` (with `node_type`, a picker of that node's backends) |
| `inputs` / `outputs` | Typed pins `{id, label, type, required?, default?, description?}`; types `text number boolean json any image images audio video mesh svg pdf file` |
| `exec` | `false` makes it a value node (no run wires; runs when something pulls its outputs) |

The handler gets `ctx = {config, inputs, payload, node, kind, files, artifact_dir}`:
`inputs` holds each input pin (wired value, else the value set in details), `payload`
the run's fields, `artifact_dir` a folder for files it makes. It returns a dict: `ok`,
one key per output pin id, and `files` for files it made. `{"ok": False, "error": …}`
stops the run with that message.

```json
"automations": {
  "triggers": [{ "id": "lore_book.entry_saved", "label": "Lore entry saved", "group": "Lore Book" }],
  "nodes": [
    { "id": "lore_book.find", "label": "Find lore", "group": "Lore Book", "exec": false,
      "inputs": [{ "id": "query", "label": "Query", "type": "text", "required": true }],
      "outputs": [{ "id": "entries", "label": "Entries", "type": "json" }, { "id": "count", "label": "Count", "type": "number" }] },
    { "id": "lore_book.save", "label": "Save lore", "group": "Lore Book",
      "config_fields": [{ "id": "tone", "label": "Tone", "type": "select", "options": ["epic", "funny", "spooky"] }],
      "inputs": [{ "id": "title", "label": "Title", "type": "text", "required": true },
                 { "id": "body", "label": "Text", "type": "text", "required": true }],
      "outputs": [{ "id": "entry", "label": "Entry", "type": "json" }] }
  ],
  "templates": [{
    "id": "lore-book-write", "label": "Write a lore entry", "category": "Lore Book",
    "graph": {
      "nodes": [
        { "id": "s", "type": "start.chat", "x": 0, "y": 0, "config": {} },
        { "id": "a", "type": "pipeline.agent", "x": 300, "y": 0, "config": { "prompt": "Write one short lore entry for the request. Reply with the entry text only." } },
        { "id": "t", "type": "input.text", "x": 300, "y": 160, "config": { "value": "New entry" } },
        { "id": "w", "type": "lore_book.save", "x": 600, "y": 0, "config": { "tone": "epic" } },
        { "id": "f", "type": "pipeline.finish", "x": 900, "y": 0, "config": {} }
      ],
      "edges": [
        { "source": "s", "target": "a", "kind": "main" },
        { "source": "a", "target": "w", "kind": "main" },
        { "source": "w", "target": "f", "kind": "main" },
        { "source": "t", "target": "w", "kind": "data", "source_pin": "text", "target_pin": "title" },
        { "source": "a", "target": "w", "kind": "data", "source_pin": "text", "target_pin": "body" }
      ]
    }
  }]
}
```

```python
def register(api) -> None:
    data = api.data

    def save_entry(title: str = "", body: str = "", tone: str = "epic") -> dict:
        key = "".join(c if c.isalnum() else "-" for c in title.strip().lower()).strip("-")
        if not key or not body.strip():
            return {"ok": False, "error": "A lore entry needs a title and text."}
        entry = {"id": key, "title": title.strip(), "body": body.strip(), "tone": tone}
        try:
            data.put(f"entry.{key}", entry)
        except PermissionError as exc:
            return {"ok": False, "error": str(exc)}
        api.emit_automation("lore_book.entry_saved", {"title": entry["title"], "tone": tone})
        return {"ok": True, "entry": entry}

    def find(query: str = "") -> dict:
        q = query.strip().lower()
        rows = [e for e in data.items("entry.").values() if q and (q in e["title"].lower() or q in e["body"].lower())]
        return {"ok": True, "entries": rows, "count": len(rows)}

    @api.tool(listener=False)
    def lore_book_save(title: str = "", body: str = "", tone: str = "epic") -> dict:
        """Save one lore entry (tone: epic, funny or spooky)."""
        return save_entry(title, body, tone)

    @api.tool(listener=False)
    def lore_book_find(query: str = "") -> dict:
        """Find lore entries whose title or text contains the query."""
        return find(query)

    @api.register_pipeline_node("lore_book.save")
    def node_save(ctx: dict) -> dict:
        inputs, config = ctx.get("inputs") or {}, ctx.get("config") or {}
        return save_entry(str(inputs.get("title") or ""), str(inputs.get("body") or ""), str(config.get("tone") or "epic"))

    @api.register_pipeline_node("lore_book.find")
    def node_find(ctx: dict) -> dict:
        return find(str((ctx.get("inputs") or {}).get("query") or ""))
```

- A trigger is a start node: a workflow whose start is `lore_book.entry_saved` runs on
  this PC each time `api.emit_automation` fires it; the payload fields are
  `{{title}}`, `{{tone}}`. Config values set on the trigger node filter the payload.
- Several workflows that call each other ship as one folder template:
  `{"id", "label", "kind": "bundle", "root": "Lore Book", "folders": ["Functions"],
  "workflows": [{"key": "save", "name": "Save lore", "folder": "Functions",
  "graph": {…}}]}`; a `workflow.call` step's `workflow_id: "@save"` names it.
  Details in `workflows`.
- Keep templates one straight line of steps; data wires carry the values.
- A config field `{"id": "art", "type": "backend", "node_type": "image.generate"}`
  shows the Text to Image backend picker; read the pick from `config["art"]` and its
  settings from `config["gateway_config"]`.

## 3. Settings and secrets

Toggles and options are native Ducky settings, never a form in your panel. API keys
are `secret` properties: stored encrypted on this PC, never readable back, declared in
`secret_keys`.

```json
"secret_keys": ["taskhub_api_key"],
"contributes": {
  "settings.tabs": [{ "id": "TaskHub", "label": "TaskHub", "icon": "duck", "ui": "sections" }],
  "settings.sections": [{
    "tab": "TaskHub", "id": "account", "title": "TaskHub", "order": 10,
    "description": "Paste your TaskHub API key (TaskHub → Settings → API). Stored encrypted on this PC.",
    "properties": [
      { "id": "taskhub_api_key", "type": "secret", "label": "API key", "placeholder": "th_…", "testable": true },
      { "id": "board", "type": "string", "label": "Board name", "default": "Island" },
      { "id": "sync_done", "type": "boolean", "label": "Also sync finished tasks", "default": false },
      { "id": "region", "type": "select", "label": "Region", "default": "us",
        "options": [{ "value": "us", "label": "US" }, { "value": "eu", "label": "Europe" }] }
    ]
  }]
}
```

```python
from __future__ import annotations

API = {"us": "https://api.taskhub.example/v1", "eu": "https://eu.api.taskhub.example/v1"}


def register(api) -> None:
    def _settings() -> dict:
        from frontend.ui_web.plugin_host_api import prefs_plugin_get

        return prefs_plugin_get(api.plugin_id)

    def _key() -> str:
        from backend.agent.secrets import get_key

        return (get_key("taskhub_api_key") or "").strip()

    def _get(path: str, key: str = "") -> dict:
        base = API.get(str(_settings().get("region") or "us"), API["us"])
        return api.http_json("GET", base + path, headers={"Authorization": f"Bearer {key or _key()}"}, timeout=15)

    def test_key(candidate: str) -> dict:
        try:
            me = _get("/me", candidate)
        except Exception as exc:
            return {"ok": False, "detail": f"TaskHub said: {exc}"}
        return {"ok": True, "detail": f"Signed in as {me.get('name', 'you')}."}

    api.register_secret_test("taskhub_api_key", test_key)

    @api.tool(listener=False)
    def taskhub_tasks() -> dict:
        """List the tasks on the board set in Settings → TaskHub."""
        if not _key():
            return {"ok": False, "error": "Add your TaskHub API key in Settings → TaskHub."}
        board = str(_settings().get("board") or "Island")
        try:
            out = _get(f"/boards/{board}/tasks")
        except Exception as exc:
            return {"ok": False, "error": str(exc)}
        done = bool(_settings().get("sync_done"))
        tasks = [t for t in out.get("tasks", []) if done or not t.get("done")]
        return {"ok": True, "board": board, "tasks": tasks}
    # + a workflow node and template for the same function.
```

- Read non-secret values with `prefs_plugin_get(api.plugin_id)` (the panel's
  `prefs.get` sees the same values). A `default` is only what Settings shows until
  the user changes it, so give each read the same fallback.
- Settings `select` options are `{value, label}`; workflow `config_fields` options
  are `{id, label}` or plain strings.
- Never log or return a key. Never put one in `api.data` or a file.
- Show the user their tab: `ducky_ui_show` with `navigate: "settings.tab"`,
  `item_id: "TaskHub"`, target `settings.tab.TaskHub`.

## 4. Connection checks

A plugin that talks to another program or service shows a row in the header
Connections menu. The probe must be cheap (a socket or health call with a short
timeout), never real work.

```python
    def probe() -> dict:
        if not _key():
            return {"online": False, "detail": "No API key yet: Settings → TaskHub."}
        try:
            _get("/health")
        except Exception as exc:
            return {"online": False, "detail": f"TaskHub unreachable: {exc}"}
        return {"online": True, "detail": "TaskHub connected."}

    api.connection(probe, label="TaskHub")
```

The same row gates Revert for changes recorded under your plugin id: while it says
offline, Ducky won't try to undo them.

## 5. Team-shared data

Nothing special to switch on: the user picks Local or one team for the plugin, and the
same `api.data` calls then read and write the team's copy (`plugin_data`). Design for
several people writing at once:

```python
    def add_note(level: str = "", text: str = "", private: bool = False) -> dict:
        if not level.strip() or not text.strip():
            return {"ok": False, "error": "Give the level and the note."}
        note = {"id": f"{int(time.time() * 1000)}", "level": level.strip(), "text": text.strip(), "at": time.time()}
        try:
            # One doc per note: two testers adding notes never overwrite each other.
            data.put(f"note.{note['id']}", note, sensitive=private)
        except PermissionError as exc:
            return {"ok": False, "error": str(exc)}
        where = "only on this PC" if private else f"in {data.scope()['label']}"
        return {"ok": True, "note": note, "saved": where}
```

- One doc per entity, ids that never collide (time-based or random), no shared
  counters or one-big-list docs.
- `sensitive=True` for anything personal: it stays on this PC and never syncs.
- Say whose copy changed (`data.scope()["label"]`) in results when it matters.
- Read-only and lost-access copies raise `PermissionError` on writes: return the
  message. Panels re-read on `plugin_scope_changed`.
