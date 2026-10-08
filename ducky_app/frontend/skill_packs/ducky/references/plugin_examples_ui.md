---
description: "Worked plugin examples with UI: a dashboard tab with live updates, a dock panel, a file editor kind, and a theme"
metadata:
  order: 5
  label: "Plugin examples: tabs, docks, editors, themes"
  default_enabled: false
  load_condition: "Building a plugin with a tab, dashboard, dock panel, file editor, theme or skin"
---

# Plugin examples: UI

Every example also needs what `ai_plugins` requires of all plugins: `agent.tools`,
an `@api.tool()` per action, a workflow node plus a template that uses it, and
`skills/<id>/SKILL.md`. The complete minimal tab plugin (manifest, backend, page,
skill) is in `ai_plugins` § A complete tab plugin; these build on it.

## 1. Dashboard tab with live updates

A tab with sections, a table, status badges, a refresh that pauses while hidden, and
pushes from the backend. Plugin id `build_board`.

**`plugin.json`** (the parts that differ from the minimal tab plugin)

```json
"ui.panels": [{ "id": "board", "title": "Build Board", "icon": "duck", "entry": "ui/index.html" }],
"header.buttons": [{ "id": "board", "title": "Build Board", "icon": "duck", "action": "panel:board", "order": 60 }],
"automations": {
  "nodes": [{ "id": "build_board.summary", "label": "Build summary", "group": "Build Board",
              "outputs": [{ "id": "text", "label": "Summary", "type": "text" }, { "id": "failing", "label": "Failing", "type": "number" }] }],
  "templates": [{ "id": "build-board-summary", "label": "Build summary", "category": "Build Board",
    "graph": { "nodes": [
      { "id": "s", "type": "start.chat", "x": 0, "y": 0, "config": {} },
      { "id": "a", "type": "pipeline.agent", "x": 200, "y": 0, "config": {} },
      { "id": "n", "type": "build_board.summary", "x": 400, "y": 0, "config": {} },
      { "id": "f", "type": "pipeline.finish", "x": 600, "y": 0, "config": {} }],
      "edges": [{ "source": "s", "target": "a", "kind": "main" }, { "source": "a", "target": "n", "kind": "main" },
                { "source": "n", "target": "f", "kind": "main" }] } }]
}
```

**`backend/__init__.py`**

```python
from __future__ import annotations

import time


def register(api) -> None:
    data = api.data

    def _push(kind: str) -> None:
        try:
            from frontend.ui_web.verse_editor.panel_events import push_agent_event

            push_agent_event({"type": "build_board_changed", "change": kind})
        except Exception:
            pass

    def list_builds(status: str = "") -> dict:
        rows = sorted(data.items("build.").values(), key=lambda r: r.get("at", 0), reverse=True)
        items = [r for r in rows if not status or r.get("status") == status]
        return {"ok": True, "items": items, "count": len(items)}

    def record_build(name: str, status: str = "passed", note: str = "") -> dict:
        if not name.strip():
            return {"ok": False, "error": "Name the build."}
        if status not in ("passed", "failed", "running"):
            return {"ok": False, "error": "status is passed, failed or running."}
        at = time.time()
        doc = {"id": f"{int(at * 1000)}", "name": name.strip(), "status": status, "note": note, "at": at}
        try:
            data.put(f"build.{doc['id']}", doc)
        except PermissionError as exc:
            return {"ok": False, "error": str(exc)}
        _push("recorded")
        return {"ok": True, "item": doc}

    def summary() -> dict:
        items = list_builds()["items"]
        failing = sum(1 for r in items if r.get("status") == "failed")
        text = f"{len(items)} builds, {failing} failing." if items else "No builds recorded yet."
        return {"ok": True, "text": text, "failing": failing}

    @api.tool(listener=False)
    def build_board_list(status: str = "") -> dict:
        """List recorded builds, newest first; status filters to passed, failed or running."""
        return list_builds(status)

    @api.tool(listener=False)
    def build_board_record(name: str, status: str = "passed", note: str = "") -> dict:
        """Record one build result (status: passed, failed or running)."""
        return record_build(name, status, note)

    @api.tool(listener=False)
    def build_board_summary() -> dict:
        """One-line summary of the builds and how many are failing."""
        return summary()

    @api.register_pipeline_node("build_board.summary")
    def node_summary(ctx: dict) -> dict:
        return summary()

    api.register_panel_rpc("list", lambda status="": list_builds(status))
    api.register_panel_rpc("record", lambda name="", status="passed", note="": record_build(name, status, note))
    api.register_panel_rpc("summary", lambda: summary())
```

**`ui/index.html`**

```html
<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <script src="../../_kit/ducky.js"></script>
  <style>
    .head { justify-content: space-between; }
  </style>
</head>
<body class="dk-page dk-stack">
  <div class="dk-row head">
    <div class="dk-tabs" role="tablist" id="tabs">
      <button class="dk-tab" role="tab" data-status="">All</button>
      <button class="dk-tab" role="tab" data-status="failed">Failing</button>
      <button class="dk-tab" role="tab" data-status="running">Running</button>
    </div>
    <button class="dk-btn dk-btn--ghost" id="refresh">Refresh</button>
  </div>
  <div id="summary" class="dk-muted"></div>
  <div id="view"><div class="dk-loading">Loading builds…</div></div>
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
    const rpc = (method, params = {}) => call("plugin.call", { method, params });
    const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
    const BADGE = { passed: "dk-badge--success", failed: "dk-badge--danger", running: "dk-badge--info" };
    let status = "";
    let timer = null;

    async function load() {
      const view = document.getElementById("view");
      try {
        const [list, sum] = await Promise.all([rpc("list", { status }), rpc("summary")]);
        document.getElementById("summary").textContent = sum.text;
        view.innerHTML = list.items.length
          ? `<table class="dk-table"><thead><tr><th>Build</th><th>Status</th><th>Note</th></tr></thead><tbody>${
              list.items.map((b) => `<tr><td>${esc(b.name)}</td><td><span class="dk-badge ${BADGE[b.status] || ""}">${esc(b.status)}</span></td><td class="dk-dim">${esc(b.note)}</td></tr>`).join("")
            }</tbody></table>`
          : '<div class="dk-empty"><div class="dk-empty__title">No builds here</div>Ask Ducky to record one.</div>';
      } catch (err) {
        view.innerHTML = `<div class="dk-error"><div class="dk-error__title">Couldn't load builds</div>${esc(err.message)}</div>`;
      }
    }
    function select(next) {
      status = next;
      for (const tab of document.querySelectorAll(".dk-tab")) tab.setAttribute("aria-selected", String(tab.dataset.status === status));
      call("prefs.set", { id: "status", value: status });
      load();
    }
    const resume = () => { if (!timer) timer = setInterval(load, 30000); load(); };
    const pause = () => { clearInterval(timer); timer = null; };

    document.getElementById("tabs").onclick = (ev) => { if (ev.target.dataset.status !== undefined) select(ev.target.dataset.status); };
    document.getElementById("refresh").onclick = load;
    window.addEventListener("message", (ev) => {
      const e = ev.data?.channel === CHANNEL ? ev.data.event : null;
      if (!e) return;
      if (e.type === "panel.visibility") e.visible ? resume() : pause();
      if (e.type === "build_board_changed" || e.type === "plugin_scope_changed") load();
    });
    call("plugin.subscribe", { types: ["build_board_changed"] });
    call("prefs.get", { id: "status" }).then((r) => select(r.value || "")).catch(() => select(""));
    resume();
  </script>
</body>
</html>
```

## 2. Dock panel

A dock is a panel shown in the left or right sidebar instead of an editor tab. Declare
the page in `ui.panels`, then point a dock at it. Keep it narrow: one column, short
labels, no wide tables.

```json
"ui.panels": [{ "id": "notes", "title": "Quick Notes", "icon": "duck", "entry": "ui/notes.html" }],
"dock.panels": [{ "id": "notes-dock", "title": "Quick Notes", "defaultSide": "right", "ui": "panel:notes" }]
```

The page is built exactly like a tab page (kit tag, bridge helper, states, visibility).
Its actions are tools and nodes like any other (`quick_notes_add`, `quick_notes_list`).

## 3. File editor kind

Opens files with given suffixes in your panel. The page learns which file from
`plugin.info` (`filePath`, relative to the open project); the backend reads and writes
it through Ducky's workspace tools, so saves land in the Changes tab and can be reverted.

```json
"ui.panels": [{ "id": "csv", "title": "Table Editor", "icon": "duck", "entry": "ui/csv.html" }],
"editor.kinds": [{ "kind": "csv_table", "title": "Table Editor", "ui": "panel:csv", "suffixes": [".csv"] }]
```

```python
def register(api) -> None:
    def read_table(path: str = "") -> dict:
        if not path.lower().endswith(".csv"):
            return {"ok": False, "error": "Open a .csv file."}
        try:
            out = api.call_tool("workspace_read_file", {"relative_path": path})
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}
        if out.get("more"):
            return {"ok": False, "error": "This file is too long for the table editor."}
        rows = [line.split(",") for line in str(out.get("content") or "").splitlines()]
        return {"ok": True, "path": path, "rows": rows}

    def write_table(path: str = "", rows: list | None = None) -> dict:
        if not path.lower().endswith(".csv"):
            return {"ok": False, "error": "Open a .csv file."}
        text = "\n".join(",".join(str(cell) for cell in row) for row in rows or []) + "\n"
        try:
            api.call_tool("workspace_write_file", {"relative_path": path, "content": text})
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}
        return {"ok": True, "path": path, "rows": len(rows or [])}

    @api.tool(listener=False)
    def table_editor_read(path: str = "") -> dict:
        """Read a project .csv file as rows of cells."""
        return read_table(path)

    @api.tool(listener=False)
    def table_editor_write(path: str = "", rows: list | None = None) -> dict:
        """Replace a project .csv file with these rows (each a list of cells)."""
        return write_table(path, rows)

    api.register_panel_rpc("read", lambda path="": read_table(path))
    api.register_panel_rpc("write", lambda path="", rows=None: write_table(path, rows))
    # + a workflow node and template, as every plugin has.
```

```js
// In ui/csv.html, after the bridge helper:
const info = await call("plugin.info");
const path = info.filePath || new URLSearchParams(location.search).get("file") || "";
const table = await rpc("read", { path });
```

In a UEFN project `workspace_write_file` writes only under `Content/` and `.ducky/`.

## 4. Theme

A theme is data in `plugin.json` (colors are allowed there). It shows in Settings →
Appearance, and the plugin's tool switches to it with `api.set_appearance_profile`.

```json
"contributes": {
  "agent.tools": { "category": "neon_themes", "intent_pattern": "\\b(neon theme|neon themes)\\b",
                   "plan_tools": ["neon_themes_list"] },
  "appearance.profiles": [{
    "id": "neon-night",
    "name": "Neon Night",
    "foundation": { "accent": "#ff3ea5", "bg": "#0b0b14", "surface": "#151526", "text": "#e9e9ff", "border": "#2a2a44" },
    "overrides": { "green": "#3dffb5", "red": "#ff5470" }
  }],
  "automations": {
    "nodes": [{ "id": "neon_themes.apply", "label": "Use a neon theme", "group": "Neon Themes",
                "config_fields": [{ "id": "theme", "label": "Theme", "type": "select", "options": [{ "id": "neon-night", "label": "Neon Night" }] }],
                "outputs": [{ "id": "profile", "label": "Theme", "type": "text" }] }],
    "templates": [{ "id": "neon-themes-apply", "label": "Switch to Neon Night", "graph": { "nodes": [
      { "id": "s", "type": "start.chat", "x": 0, "y": 0, "config": {} },
      { "id": "a", "type": "pipeline.agent", "x": 200, "y": 0, "config": {} },
      { "id": "n", "type": "neon_themes.apply", "x": 400, "y": 0, "config": { "theme": "neon-night" } },
      { "id": "f", "type": "pipeline.finish", "x": 600, "y": 0, "config": {} }],
      "edges": [{ "source": "s", "target": "a", "kind": "main" }, { "source": "a", "target": "n", "kind": "main" },
                { "source": "n", "target": "f", "kind": "main" }] } }]
  }
}
```

`foundation` keys are `accent`, `bg`, `surface`, `text`, `border`; `overrides` use
Appearance variable names without `--` (`plugin_look`).

```python
THEMES = {"neon-night": "Neon Night"}


def register(api) -> None:
    def apply(theme: str = "") -> dict:
        if theme not in THEMES:
            return {"ok": False, "error": f"Pick one of: {', '.join(THEMES)}."}
        out = api.set_appearance_profile(theme)
        return {**out, "profile": THEMES[theme]} if out.get("ok") else out

    @api.tool(listener=False)
    def neon_themes_list() -> dict:
        """List this plugin's Appearance themes (id and name)."""
        return {"ok": True, "themes": [{"id": k, "name": v} for k, v in THEMES.items()]}

    @api.tool(listener=False)
    def neon_themes_apply(theme: str = "") -> dict:
        """Switch Ducky's Appearance to one of this plugin's themes (an id from neon_themes_list)."""
        return apply(theme)

    @api.register_pipeline_node("neon_themes.apply")
    def node_apply(ctx: dict) -> dict:
        return apply(str((ctx.get("config") or {}).get("theme") or ""))
```

No default theme on purpose: `ducky_plugin_test` calls tools and nodes with empty
input, and a test must never switch the user's theme. Tell the user when you switch
it; they can switch back in Settings → Appearance.

`appearance.css` (`[{"entry": "theme/extra.css"}]`) adds CSS to the app shell; use
variables in it and keep it small. Effects and skins (`appearance.effects`,
`appearance.skin`) are scripts that run in the app window: build one only when asked.
