---
description: "Build AI-made desktop plugins: the path, the hard rules, plugin.json, the backend api and panel bridge, a complete tab plugin, and which plugin reference to load next. ducky_plugin_* only."
metadata:
  order: 3
  label: "AI-made plugins"
  default_enabled: true
  load_condition: "User asks to create, change, fix, test or share a desktop plugin, theme, skin, tab, panel, dock, workflow node, or add app functionality via plugins"
---

## AI-made desktop plugins

You build plugins **for this install** with `ducky_plugin_*` only. Drafts are
shared across chats; the tools write the files and you never open those folders.
A draft runs only on its author's PC. Other people get a plugin only through the
UEFN Ducky Store: `ducky_plugin_build` → `ducky_plugin_publish` to one team (members
only; it pauses for anyone who loses access) or public (anyone; the owner reviews
and rebuilds it). Every Store download carries a license, free ones too. See
`plugin_publishing`.

### Load the reference you need

| Need | `skill_read_subskill("ducky", …)` |
|---|---|
| UI kit, every Appearance variable, panel states, hidden tabs, themes | `plugin_look` |
| `api.data`, Local or one team, `sensitive`, encryption, lost team access | `plugin_data` |
| Build, publish to a team or public, licenses, what compiles | `plugin_publishing` |
| Validate, test, read errors, fix loop | `plugin_testing` |
| Known problems (read before you say it's done) | `plugin_known_problems` |
| Examples: tab app or dashboard, dock panel, file editor, theme or skin | `plugin_examples_ui` |
| Examples: tools only, workflow nodes and templates, settings and secrets, connection checks, team-shared data | `plugin_examples_tools` |
| Examples: Verse templates, UEFN listener tools, Blender and Meshy pipelines | `plugin_examples_uefn` |
| Examples: image generators, AI providers, sounds and hooks, walkthroughs | `plugin_examples_app` |
| Workflow graphs, nodes, pins, templates in depth | `workflows` |

### HARD rules (do all of them or the plugin is not done)

1. **Census first.** `ducky_plugin_list` lists `drafts` and `installed`. Reuse a
   matching id; empty → `ducky_plugin_scaffold`. Never glob, shell, PowerShell,
   IDE-Read or `workspace_write_file` into AppData `ai_plugins` / `uefn_plugins`.
2. **Edit only with** `ducky_plugin_write_file` / `ducky_plugin_read_file` /
   `ducky_plugin_list(id=…)`.
3. **Blend with Ducky's tools.** Before writing a feature, search for it:
   `ducky_find_tools(query)` (and `ducky_store_search` for whole plugins). When a
   tool exists (Meshy, Blender, UEFN import, workspace files, workflows, Store…),
   call it from your backend with `api.call_tool(name, args)` instead of rebuilding
   it. It runs under the same approval rules the agent has.
4. **Every user action is an MCP tool and a workflow node.** `@api.tool()` for each
   action (list / get / create / update / delete, not a lone ping); a tool that
   doesn't touch the UEFN editor is `@api.tool(listener=False)`. Panel buttons call
   `plugin.call` RPCs that run the **same** functions. Each action also has a workflow
   node (`contributes.automations.nodes` + `@api.register_pipeline_node`) and a
   template that uses it (`start.chat` → `pipeline.agent` → your node →
   `pipeline.finish`). Themes too.
5. **Always Ducky's look.** Every panel page links the UI kit
   (`<script src="../../_kit/ducky.js"></script>`) and colors only with Appearance
   variables (`var(--bg)`, `var(--fg)`, `var(--card)`, `var(--border)`,
   `var(--accent)`, `var(--font-ui)`, …). Never `#hex`, `rgb()`, `hsl()` or named
   colors, never a made-up `var(--x)`: `ducky_plugin_validate` rejects them.
   Theme keys from `theme.get` / `appearance_theme` come **without** `--`. Full list
   and rules: `plugin_look`.
6. **Bundled skill** `skills/<id>/SKILL.md` (the scaffold writes a stub): name each
   tool and when to use it, so later chats know them. Not a `ducky_skills_*` pack.
7. **Mutators record a changeset** (`api.changeset.record(…)` with an `inverse`, slot
   `{plugin_id}://{kind}/{id}/{facet}`) so Revert can undo them.
8. **Data only through `api.data`** (panels: `data.*` / `files.*`). Never a folder or
   file you pick. One doc per record. The user picks Local or exactly one team per
   plugin (never mixed; switching restarts the plugin on the other copy), and prefs
   and cache follow that choice. Team copies are encrypted per team and lock when
   access is lost; `sensitive=True` docs stay on this PC. See `plugin_data`.
9. **Write code that compiles.** Published backends are compiled: never read your own
   `.py` source, never load `.py` files by path, import your modules relatively
   (`from . import store`), keep data as `.json` next to the module or in `assets/`.
   `listener/` code runs in UEFN's Python and Blender code runs in Blender. See
   `plugin_publishing`.
10. **Test after every change** (`plugin_testing`): `ducky_plugin_validate(id)` →
    `ducky_plugin_test(id)` → `ducky_plugin_errors(id)`. Fix and repeat until all three
    are clean before you say it works.
11. **Never** git-clone `uefn-plugin-*`, run `publish_plugin.sh`, Install-from-file,
    `cp` into AppData, upload zips, or edit `ducky_app/` / the EXE to add a tab.
    Never `ducky_skills_create_pack` unless they asked for a skill pack.

### Path (no forks)

1. `ducky_plugin_list`: reuse an existing draft or id, or
2. `ducky_plugin_scaffold(id, label, description)`: writes `plugin.json` (tools,
   one node, a template graph), `backend/__init__.py` and `skills/<id>/SKILL.md`.
3. `ducky_plugin_write_file` until every file below is right. Fix the scaffold's
   panel RPC lambdas and add `listener=False` (see `plugin_known_problems`).
4. `ducky_plugin_validate(id)`: every error says how to fix it.
5. `ducky_plugin_install(id)`, then `ducky_store_set_enabled(id, true)`. If it answers
   `needs_trust`, **stop**: the user confirms the plugin once.
6. `ducky_plugin_test(id)`: reinstalls, calls each tool with sample input, runs each
   node in a throwaway data scope, opens each panel, checks the UI files.
7. `ducky_plugin_errors(id)`: load errors, panel errors, tool and node exceptions.
8. Iterate on the **draft** → validate → test again. Reinstall reloads it; never send
   the user to the Store for updates.

Uninstall: `ducky_store_remove(id, confirm=true)`. Delete the draft only:
`ducky_plugin_delete_draft(id, confirm=true)`. Id: `^[a-z][a-z0-9_-]{0,63}$`; it
can't reuse a Store or local plugin's id (uninstall first). No secrets in files
(`.dat`, `.env`, `.pem`, `.key` are refused); keys go in `settings.sections`
`secret` fields.

### Files

| Path | What |
|---|---|
| `plugin.json` | Manifest (below). |
| `backend/__init__.py` | `register(api)`: tools, nodes, panel RPCs. More modules beside it, imported relatively. |
| `ui/*.html` (+ css/js) | Panel pages, only if they asked for a tab, dock or editor. |
| `assets/` | Icons, sounds, data files. |
| `skills/<id>/SKILL.md` | The plugin's own skill. |
| `listener/` | Optional handlers that run inside UEFN (`plugin_examples_uefn`). |

### plugin.json

Top level: `id`, `"kind": "plugin"`, `version`, `label`, `description`,
`min_app_version`, `"default_enabled": false`, `secret_keys` (ids of `secret`
settings), `contributes`, `"backend": {"entry": "backend", "register": "register"}`.

| `contributes` key | What | Example in |
|---|---|---|
| `agent.tools` | `{category, intent_pattern, plan_tools?, destructive_tools?}`: when chats get your tools, which read-only ones Plan mode may call, which ones ask the user first (tests skip those) | here |
| `ui.panels` + `header.buttons` | A tab and the header button that opens it (`"action": "panel:<id>"`) | here |
| `dock.panels` | A side dock (`defaultSide`, `"ui": "panel:<id>"`) | `plugin_examples_ui` |
| `editor.kinds` | Opens files by suffix in your panel | `plugin_examples_ui` |
| `automations.nodes` / `.templates` / `.triggers` / `.image_generators` | Workflow nodes, starters, triggers, Text to Image backends | `plugin_examples_tools`, `plugin_examples_app` |
| `settings.tabs` + `settings.sections` | Native settings: `boolean`, `string`, `select`, `secret` | `plugin_examples_tools` |
| `walkthrough` | A tour on first enable | `plugin_examples_app` |
| `verse.templates` | Verse files and multi-file packs for New file | `plugin_examples_uefn` |
| `appearance.profiles` / `.css` / `.effects` / `.skin` | Themes, app CSS, effects, skins | `plugin_examples_ui` |
| `sounds` + `hooks` | Sound files and named moments to play them on | `plugin_examples_app` |
| `chat.references` | `@` mentions in the chat box | — |
| `llm.providers`, `llm.coding_agents`, `ide.hookups`, `tts.voices`, `shell.boot` | Only when asked | `plugin_examples_app` |

### The backend `api`

| Call | Use |
|---|---|
| `@api.tool(name=None, intent=None, listener=True)` | MCP tool. Docstring = description. `listener=False` unless it calls the UEFN editor. Return a dict. |
| `@api.register_pipeline_node("<id>.<node>")` | Workflow node handler `fn(ctx) -> dict` (alias `register_automation_node`). |
| `api.register_panel_rpc(name, fn)` | Panel `plugin.call` target. `fn` is called with **keyword** arguments: `fn(**params)`. |
| `api.call_tool(name, args)` | Call any Ducky MCP tool in-process; returns the parsed result, raises `ValueError` on failure. |
| `api.data` | Docs and files in the plugin's data scope (`plugin_data`). |
| `api.changeset.record(command=, kind=, ident=, facet=, before=, inverse=, slot=)` | Changes ledger entry; never raises. |
| `api.listener(command, params, timeout=None)` | One command to the live UEFN listener. |
| `api.http_json(method, url, …)`, `api.poll(…)` | HTTP JSON and polling helpers. |
| `api.emit_automation(trigger_id, payload)` | Run this PC's workflows that start on your trigger. |
| `api.connection(fn, label=, program=)` | Header Connections row; `fn()` is cheap and returns `{online, detail}`. |
| `api.register_secret_test(secret_key, fn)` | Settings → Test for a key: `fn(api_key) -> {ok, detail}`. |
| `api.spotlight(window=, box=, title=, body=, steps=, click=, wait=)` | Highlight a control in UEFN, Blender or any window (same as `ducky_ui_show`). |
| `api.log(msg)`, `api.is_enabled()`, `api.plugin_id` | Logging, enabled check, your id. |

Secrets: `from backend.agent.secrets import get_key` then `get_key("<secret id>")`.
Non-secret settings values: `from frontend.ui_web.plugin_host_api import
prefs_plugin_get` then `prefs_plugin_get(api.plugin_id).get("<property id>")`.
Push a live update to your panels: `from frontend.ui_web.verse_editor.panel_events
import push_agent_event` then `push_agent_event({"type": "<id>_changed", …})`; the
panel subscribes with `plugin.subscribe`.

### The panel bridge

Panels are sandboxed iframes that talk to the host with `postMessage` on channel
`uefn-plugin-ui` (helper in the example below).

| Method | Params → result |
|---|---|
| `plugin.call` | `{method, params}` → your RPC's return |
| `plugin.info` | → `{pluginId, panelId, version, filePath}` (`filePath` in file editors) |
| `plugin.subscribe` | `{types: [...]}`: host push events you want |
| `theme.get` | → `{vars}` (keys without `--`; the kit applies them) |
| `prefs.get` / `prefs.set` | `{id}` / `{id, value}`: small per-plugin UI choices (follow the data scope) |
| `scope.get` | → `{scope: {kind, label, teamId, readOnly}}` |
| `data.get` / `data.put` / `data.list` / `data.delete` | One JSON doc per key (≤ 1 MB) |
| `files.put` / `files.get` / `files.list` / `files.delete` | Base64 files (≤ 100 MB) |
| `cache.get` / `cache.set` / `cache.clear` | Throwaway cache |
| `llm.complete` | `{system, user, model?}` → text from the user's chosen model |

Host pushes: `appearance_theme`, `panel.visibility` (`visible`),
`plugin_scope_changed` (re-read your data), and the types you subscribed to.
Visited tabs stay alive while hidden: pause timers, polling and media when
`panel.visibility` says `visible: false`. Moving a tab to a floating window reloads
the page: keep state in `prefs` / `data`, never only in page memory. Every panel
page also reports its uncaught errors, rejected promises and `console.error` to
`ducky_plugin_errors`.

### A complete tab plugin

**`plugin.json`**

```json
{
  "id": "card_shop",
  "kind": "plugin",
  "version": "1.0.0",
  "label": "Card Shop",
  "description": "Cards for the island's shop: list, add, edit, remove.",
  "min_app_version": "1.0.0",
  "default_enabled": false,
  "secret_keys": [],
  "contributes": {
    "agent.tools": {
      "category": "card_shop",
      "intent_pattern": "\\b(card shop|shop cards?)\\b",
      "plan_tools": ["card_shop_list"],
      "destructive_tools": ["card_shop_delete"]
    },
    "ui.panels": [{ "id": "main", "title": "Card Shop", "icon": "duck", "entry": "ui/index.html" }],
    "header.buttons": [{ "id": "main", "title": "Card Shop", "icon": "duck", "action": "panel:main", "order": 50 }],
    "automations": {
      "nodes": [
        { "id": "card_shop.list", "label": "List cards", "group": "Card Shop",
          "config_fields": [{ "id": "rarity", "label": "Rarity", "type": "string" }],
          "outputs": [{ "id": "items", "label": "Cards", "type": "json" }, { "id": "count", "label": "Count", "type": "number" }] },
        { "id": "card_shop.save", "label": "Save card", "group": "Card Shop",
          "inputs": [{ "id": "card", "label": "Card", "type": "json", "required": true }],
          "outputs": [{ "id": "item", "label": "Card", "type": "json" }] }
      ],
      "templates": [{
        "id": "card-shop-list", "label": "List shop cards", "category": "Card Shop",
        "description": "Chat start → Agent → List cards → Finish.",
        "graph": {
          "nodes": [
            { "id": "s", "type": "start.chat", "x": 0, "y": 0, "config": {} },
            { "id": "a", "type": "pipeline.agent", "x": 200, "y": 0, "config": {} },
            { "id": "n", "type": "card_shop.list", "x": 400, "y": 0, "config": {} },
            { "id": "f", "type": "pipeline.finish", "x": 600, "y": 0, "config": {} }
          ],
          "edges": [
            { "source": "s", "target": "a", "kind": "main" },
            { "source": "a", "target": "n", "kind": "main" },
            { "source": "n", "target": "f", "kind": "main" }
          ]
        }
      }]
    }
  },
  "backend": { "entry": "backend", "register": "register" }
}
```

**`backend/__init__.py`**

```python
from __future__ import annotations

import re


def register(api) -> None:
    data = api.data
    pid = api.plugin_id

    def _key(card_id: str) -> str:
        return re.sub(r"[^a-z0-9_-]+", "-", str(card_id or "").strip().lower()).strip("-")

    def _record(command: str, card_id: str, before, inverse: list) -> None:
        api.changeset.record(command=command, kind="card", ident=card_id, facet="record",
                             before=before, inverse=inverse, slot=f"{pid}://card/{card_id}/record")

    def list_cards(rarity: str = "") -> dict:
        rows = list(data.items("card.").values())
        items = [r for r in rows if not rarity or r.get("rarity") == rarity]
        return {"ok": True, "items": items, "count": len(items)}

    def save_card(card_id: str, fields: dict | None = None) -> dict:
        cid = _key(card_id)
        if not cid:
            return {"ok": False, "error": "Give the card an id (its name works)."}
        before = data.get(f"card.{cid}")
        doc = {**(before or {}), **(fields or {}), "id": cid}
        data.put(f"card.{cid}", doc)
        undo = ({"command": "card_shop_save", "params": {"card_id": cid, "fields": before}} if before
                else {"command": "card_shop_delete", "params": {"card_id": cid}})
        _record("card_shop_save", cid, before, [undo])
        return {"ok": True, "item": doc}

    def delete_card(card_id: str) -> dict:
        cid = _key(card_id)
        before = data.get(f"card.{cid}") if cid else None
        if before is None:
            return {"ok": False, "error": f"No card {cid or card_id!r}."}
        data.delete(f"card.{cid}")
        _record("card_shop_delete", cid, before,
                [{"command": "card_shop_save", "params": {"card_id": cid, "fields": before}}])
        return {"ok": True, "id": cid}

    @api.tool(intent=r"\b(card shop|shop cards?)\b", listener=False)
    def card_shop_list(rarity: str = "") -> dict:
        """List the shop's cards, optionally only one rarity."""
        return list_cards(rarity)

    @api.tool(intent=r"\b(card shop|shop cards?)\b", listener=False)
    def card_shop_save(card_id: str, fields: dict | None = None) -> dict:
        """Create or update one card (fields: name, rarity, price, …)."""
        return save_card(card_id, fields)

    @api.tool(intent=r"\b(card shop|shop cards?)\b", listener=False)
    def card_shop_delete(card_id: str) -> dict:
        """Remove one card by id."""
        return delete_card(card_id)

    @api.register_pipeline_node("card_shop.list")
    def node_list(ctx: dict) -> dict:
        return list_cards(str((ctx.get("config") or {}).get("rarity") or ""))

    @api.register_pipeline_node("card_shop.save")
    def node_save(ctx: dict) -> dict:
        card = (ctx.get("inputs") or {}).get("card") or {}
        return save_card(str(card.get("id") or ""), card)

    api.register_panel_rpc("list", lambda rarity="": list_cards(rarity))
    api.register_panel_rpc("save", lambda card_id="", fields=None: save_card(card_id, fields))
    api.register_panel_rpc("delete", lambda card_id="": delete_card(card_id))
```

**`ui/index.html`**

```html
<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <script src="../../_kit/ducky.js"></script>
  <style>
    .grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(180px, 1fr)); gap: 8px; }
  </style>
</head>
<body class="dk-page dk-stack">
  <div class="dk-row">
    <input class="dk-input" id="name" placeholder="Card name" aria-label="Card name">
    <button class="dk-btn dk-btn--primary" id="add">Add card</button>
  </div>
  <div id="view"><div class="dk-loading">Loading cards…</div></div>
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

    async function load() {
      const view = document.getElementById("view");
      try {
        const out = await rpc("list");
        view.innerHTML = out.items.length
          ? `<div class="grid">${out.items.map((c) => `<div class="dk-card"><div class="dk-card__title">${esc(c.name || c.id)}</div><span class="dk-muted">${esc(c.rarity || "common")}</span></div>`).join("")}</div>`
          : '<div class="dk-empty"><div class="dk-empty__title">No cards yet</div>Add one above or ask Ducky to make a set.</div>';
      } catch (err) {
        view.innerHTML = `<div class="dk-error"><div class="dk-error__title">Couldn't load cards</div>${esc(err.message)}</div>`;
      }
    }
    document.getElementById("add").onclick = async () => {
      const input = document.getElementById("name");
      const name = input.value.trim();
      if (!name) return input.focus();
      const out = await rpc("save", { card_id: name, fields: { name } }).catch((err) => ({ ok: false, error: err.message }));
      if (out.ok === false) {
        document.getElementById("view").insertAdjacentHTML("afterbegin", `<div class="dk-error">${esc(out.error)}</div>`);
        return;
      }
      input.value = "";
      load();
    };
    window.addEventListener("message", (ev) => {
      const e = ev.data?.channel === CHANNEL ? ev.data.event : null;
      if (e?.type === "plugin_scope_changed") load();
    });
    load();
  </script>
</body>
</html>
```

**`skills/card_shop/SKILL.md`**

```markdown
---
name: card_shop
description: "Card Shop: the island shop's cards. List, add, edit and remove them with card_shop_* tools."
---

# Card Shop

- `card_shop_list(rarity?)`: every card, or one rarity.
- `card_shop_save(card_id, fields)`: create or update a card (name, rarity, price).
- `card_shop_delete(card_id)`: remove a card. Revert in Changes undoes it.
- Workflow nodes `card_shop.list` and `card_shop.save` do the same in workflows.
```

Rename `card_shop` / `card` to the domain. Add `get` when records are large.
