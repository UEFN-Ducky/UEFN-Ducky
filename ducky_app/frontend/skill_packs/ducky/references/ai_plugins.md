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
A draft runs only on its author's PC and needs no license. Other people get a plugin
only through the UEFN Ducky Store: the Ducky Account plugin's publishing tools build it
compiled and upload it. Every Store download carries a license, baked in at build time,
free ones too. See `plugin_publishing`.

### Load the reference you need

| Need | `skill_read_subskill("ducky", …)` |
|---|---|
| UI kit, every Appearance variable, panel states, hidden tabs, themes | `plugin_look` |
| `api.data`, Local or one shared space, `sensitive`, encryption, lost access | `plugin_data` |
| Share through the Store, licenses, what compiles | `plugin_publishing` |
| Validate, test, read errors, fix loop | `plugin_testing` |
| Known problems (read before you say it's done) | `plugin_known_problems` |
| Examples: tab app or dashboard, dock panel, file editor, theme or skin | `plugin_examples_ui` |
| Examples: tools only, workflow nodes and templates, settings and secrets, connection checks, shared data | `plugin_examples_tools` |
| Examples: Verse templates, UEFN listener tools, Blender and Meshy pipelines | `plugin_examples_uefn` |
| Examples: image generators, AI providers, sounds and hooks, walkthroughs | `plugin_examples_app` |
| Workflow graphs, nodes, pins, templates in depth | `workflows` |

### HARD rules (do all of them or the plugin is not done)

0. **Read every reference before the first file.** A new plugin, or a change that adds a
   panel, data, a tool or a node: load `plugin_data`, `plugin_look`, `plugin_testing` and
   `plugin_known_problems` (plus the `plugin_examples_*` that matches) in the same turn,
   before `ducky_plugin_scaffold` or any write. Then `ducky_plugin_reference` for the live
   `api.*` list. Never build from memory: every rule below points at one of them, and a
   plugin that misses one is not done.
1. **Census first.** `ducky_plugin_list` lists `drafts` and `installed`. Reuse a
   matching id; empty → `ducky_plugin_scaffold`. Never glob, shell, PowerShell,
   IDE-Read or `workspace_write_file` into AppData `ai_plugins` / `uefn_plugins`.
2. **Edit only with** `ducky_plugin_edit_file` (change part of a file) /
   `ducky_plugin_write_file` (new files) / `ducky_plugin_read_file` (pass
   `start_line`/`end_line` for big files: read the part you change, not the whole file) /
   `ducky_plugin_move_file` / `ducky_plugin_delete_file` / `ducky_plugin_list(id=…)`.
   Development evidence and check scripts live under `tests/` (never shipped), and no
   file may hold a personal path such as `C:/Users/<name>`: validate warns, publishing refuses.
3. **Blend with Ducky's tools.** Before writing a feature, search for it:
   `ducky_find_tools(query)` (and `ducky_store_search` for whole plugins). When a
   tool exists (Meshy, Blender, UEFN import, workspace files, workflows, Store…),
   call it from your backend with `api.call_tool(name, arguments)` instead of rebuilding
   it. It runs under the same approval rules the agent has. `ducky_store_get(slug)` reads
   one Store plugin; installing or updating one (`ducky_store_install` /
   `ducky_store_update`) is the user's call: ask first.
4. **Every user action is an MCP tool and a workflow node.** `@api.tool()` for each
   action (list / get / create / update / delete, not a lone ping). Pass
   `listener=True` only when the tool cannot start without the UEFN editor (`api.listener`
   or a UEFN tool): Ducky refuses those while UEFN is closed; every other tool always runs. Panel buttons call
   `plugin.call` RPCs that run the **same** functions. Each action also has a workflow
   node (`contributes.automations.nodes` + `@api.register_pipeline_node`) and a
   template that uses it (`start.chat` → `pipeline.agent` → your node →
   `pipeline.finish`). Themes too. Every `api.data` doc a person would ask for by name
   has a `name` field, so `ducky_find` finds it (see "Make the person's things findable").
5. **Always Ducky's look.** Every panel page links the UI kit
   (`<script src="../../_kit/ducky.js"></script>`) and colors only with Appearance
   variables (`var(--bg)`, `var(--fg)`, `var(--card)`, `var(--border)`,
   `var(--accent)`, `var(--font-ui)`, …). Never `#hex`, `rgb()`, `hsl()` or named
   colors, never a made-up `var(--x)`: `ducky_plugin_validate` rejects them. Lay it
   out like the app: side panels as floating cards, a frameless main area, pill tabs,
   icon rows with a check instead of radio circles (`plugin_look` §3).
   Theme keys from `theme.get` / `appearance_theme` come **without** `--`. If the
   user specified colors, ship them as an Appearance theme (`appearance.profiles` /
   `appearance.css`) and keep the panel on the variables. Always tell the user the
   panel follows their Appearance theme when you start the UI or depart from it.
   Full list and rules: `plugin_look`.
6. **Bundled skill** `skills/<id>/SKILL.md` (the scaffold writes a stub): name each
   tool and when to use it, so later chats know them. Not a `ducky_skills_*` pack.
7. **Mutators record a changeset** (`api.changeset.record(…)` with an `inverse`, slot
   `{plugin_id}://{kind}/{id}/{facet}`) so Revert can undo them.
8. **Ready for teams: always ask Team or Local only.** Before you scaffold, **always**
   ask, even when the answer looks obvious and even for a small tool (in the same
   `ducky_ask_user` as your other questions), with two options: **For my teams** ("Your
   team shares <plugin>'s data: everyone gets the same records with changes, locks,
   history and live editing, like source control.") and **Only on this PC** ("Its data
   stays Local and never syncs."). Never decide it yourself, never skip the question, and
   never start writing before the answer. Pass it as `ducky_plugin_scaffold(…,
   data_sharing=true|false)` (it becomes `"data_sharing"` in `plugin.json`). A change to an
   existing plugin keeps its `data_sharing`; ask again only if they want it changed. Then:
   - **Data only through `api.data`** (panels: `data.*` / `files.*`). One doc per
     record, named (`name`/`title`), so locks and conflicts are per record. Never a
     folder or file you pick, never inside the island folder.
   - Files that must stay real files (an outside program reads them) go in a declared
     `team_folders` folder under `{LOCALAPPDATA}/UEFN-Ducky/<id>/` (`plugin_data`).
   - Items of this PC only (diagnostics, "last opened") go under `data_local`.
   - Panels follow teammates' changes without closing what is open
     (`plugin_scope_changed` reason `synced`).
   `ducky_plugin_validate` warns about each gap, and a Store build (public or private)
   refuses a plugin with no `data_sharing` or a team folder inside the island. The user
   picks Local or one shared space per plugin; prefs and cache follow that choice.
   Shared copies are encrypted per space and lock when access is lost;
   `sensitive=True` docs stay on this PC. See `plugin_data`.
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
12. **Nothing from the internet at runtime.** Every library, stylesheet and font the
    panel uses ships inside the plugin: `ducky_plugin_vendor(id, package, version,
    files)` downloads an exact npm version (never `latest` or a range) into
    `ui/vendor/<package>@<version>/` with its license, and returns the `<script>` /
    `<link>` tag. Panels block CDN scripts, outside stylesheets and `import` from URLs,
    and `ducky_plugin_validate` rejects them. Live data from a service goes through the
    backend (`api.http_json`), never a panel `fetch`.
13. **No personal data in logs or errors.** Never log or return the user's chat text,
    keys, emails or files in errors; keys live in `secret_keys`.
14. **A Store icon**: `assets/icon.png` (or `.svg` / `.jpg`, up to 700 KB). The Store
    rejects a new plugin without one, and `ducky_plugin_validate` warns. With none, ask
    the user (in the same `ducky_ask_user` as rule 8) whether to make one or use a
    picture they have. Make one only on their yes: `ducky_ai_image(prompt,
    confirm_spend=true)` is billed to their Ducky AI credit (a Text to Image workflow
    node with their own image model works too). A simple bold symbol on a plain
    background reads best small. Then `ducky_plugin_set_icon(id, image_path)` with the
    path it returned or they gave: it fits the picture into a 512 px PNG.
15. **Team-ready the moment it's finished** (`data_sharing: true`). A plugin is multiplayer
    and source-control ready when the AI says it's done, with no second pass. Check every
    line, the way Kanban and Island Media Kit do it (`plugin_data` has each in full):
    - **Data:** every record through `api.data`, one doc per record with a `name`, written
      in one call per user action (one call = one change in History). Related docs in the
      same call. Big blobs in `put_file`, never in a doc.
    - **Locks and conflicts:** fields people edit apart live in separate keys or `id`'d
      objects; `data_locks` set for append-only prefixes (`"none"`) and for records that
      must never be edited by two at once (`"required"`). Every tool catches
      `PermissionError` (locked or read-only) and returns `{"ok": False, "error": …}`;
      panels show that message.
    - **Per-PC state** (last opened, window layout, diagnostics) under `data_local`.
    - **Teammates' changes:** panels handle `plugin_scope_changed` — `synced`: merge the
      `keys` that changed and keep the open item, selection and scroll (Kanban re-reads
      quietly, never while a drag runs); `switched`: start over. A backend cache registers
      `api.data.on_change`.
    - **Live:** `data-live-id` on everything people point at (Kanban: each card, column
      and modal); `data-live-view` on `<body>` as `<record>:<tab>` and a
      `ducky-live-goto` handler that opens that record and tab (Island Media Kit); a
      component the group watches together (a player, a selection, an open panel) through
      `live.share` / `live.shared`; each person's own view (a camera, a tool) through
      `live.setMyState` / `live.memberStates`.
    - **Findable and undoable:** `api.search.upsert` for every record a person would search
      by meaning; `api.changeset.record` with an `inverse` for every mutator (rule 7).
    - **Tested:** `ducky_plugin_test` runs on a throwaway copy; add one test that writes a
      record, simulates a `synced` push and checks the panel kept what was open.
    With `data_sharing: false`, skip only the Live and lock lines; the rest still apply.

### Path (no forks)

0. Load the references (rule 0) and ask, in one `ducky_ask_user`: Team or Local only
   (rule 8), the icon (rule 14), and anything else only the person can decide.
1. `ducky_plugin_list`: reuse an existing draft or id, or
2. `ducky_plugin_scaffold(id, label, description, data_sharing)`: writes `plugin.json`
   (tools, one node, a template graph, the team answer from rule 8),
   `backend/__init__.py` and `skills/<id>/SKILL.md`.
3. `ducky_plugin_write_file` until every file below is right; `ducky_plugin_vendor`
   for any library the panel needs (rule 12); `ducky_plugin_set_icon` (rule 14).
4. `ducky_plugin_validate(id)`: every error says how to fix it.
5. `ducky_plugin_install(id)`, then `ducky_store_set_enabled(id, true)`. If it answers
   `needs_trust`, **stop**: the user confirms the plugin once.
6. `ducky_plugin_test(id)`: reinstalls, calls each tool with sample input, runs each
   node, opens each panel and checks the UI files, all on a throwaway copy of the
   plugin's data (docs, files, sensitive docs, cache and prefs). If it answers
   `needs_trust` ("confirm once to test this plugin"), **stop** until the user
   confirms once, then test again.
7. `ducky_plugin_errors(id)`: load errors, panel errors, tool and node exceptions.
8. Iterate on the **draft** → validate → test again. Reinstall reloads it; never send
   the user to the Store for updates.
9. Go through rule 15 line by line and `plugin_known_problems` before you say it's done;
   your reply says whether it is for the team or Local only, and what teammates get.

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
`"data_sharing"`: `true` (teammates can share its data) or `false` (always Local); a
Store build refuses a plugin without it (rule 8). Optional `data_locks`, `data_local`,
`team_folders` (`plugin_data`), `tool_concurrency` (see "Many agents at once").

| `contributes` key | What | Example in |
|---|---|---|
| `agent.tools` | `{category, intent_pattern, plan_tools?, destructive_tools?}`: when chats get your tools, which read-only ones Plan mode may call, which ones ask the user first (tests skip those) | here |
| `ui.panels` + `header.buttons` | A tab and the header button that opens it (`"action": "panel:<id>"`). The button's Show me / tour target is `header.button.<plugin id>.<button id>` (just `header.button.<button id>` in the plugin's own walkthrough) | here |
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
| `@api.tool(name=None, intent=None, listener=False)` | MCP tool. Docstring = description. `listener=True` only if it needs the UEFN editor open. Return a dict. |
| `@api.register_pipeline_node("<id>.<node>")` | Workflow node handler `fn(ctx) -> dict` (alias `register_automation_node`). |
| `api.register_panel_rpc(name, fn)` | Panel `plugin.call` target. `fn` is called with **keyword** arguments: `fn(**params)`. |
| `api.call_tool(name, arguments=None)` | Call any Ducky MCP tool in-process, with the same approvals as the agent; returns the parsed result, raises `ValueError` on failure. |
| `api.data` | Docs and files in the plugin's data scope (`plugin_data`). Tools of a plugin that uses it get `scope` (`local` or the space's name) added to their dict results. Give each doc a `name` (or `title` / `label`): `ducky_find` then finds it by name for every agent, in Local and the person's teams, with no code of yours. |
| `api.live.join(room="")` / `.leave()` / `.send(topic, data, to="")` / `.on(topic, fn)` / `.state()` | Live editing on a team copy (`plugin_data`, Live): short messages to teammates live in the group, peer to peer (JSON ≤ 16 KB, ≤ 30/s; `ducky.*` is the host's). `fn(sender, data)`. No room (or a teammate's group, `h:<device>`): this PC goes live on the team copy, every plugin with it (`.leave()` takes them all out); any other room is the plugin's own. Writes through `api.data` reach the group at once and its leader sends them to source control (`"data_live": false` in `plugin.json`: sync only, the group pulls each one as soon as it's committed). |
| `api.live.share(key, value)` / `.shared()` / `.on_shared(fn)` | The group's value of one of the plugin's components (a player playing, the picked clip: JSON ≤ 8 KB, `None` drops it, 64 keys), through the group's leader so everyone ends on the same one; `fn(key, value, by)`. Never stored or synced. |
| `api.live.set_my_state(obj)` / `.member_states()` / `.on_member_state(fn)` | Each PC's own live state in the plugin (a camera, the tool in hand: an object ≤ 4 KB, latest wins, `None` clears it); everyone in the group sees everyone's, latecomers within 5 s, `fn(sender, state)` (`state` None: they left). Never stored or synced. |
| `api.search.upsert(id, title, text, meta=None)` / `.delete(id)` / `.clear()` / `.query(text, limit=10)` | Make the plugin's own records (pages, cards, entries) findable by meaning and keywords. Only this plugin's records in its data scope for the signed-in person; kept on an index rebuild, removed on uninstall. Upserting unchanged text is free, so re-sync everything on start. `ducky_find` returns them too; add your own tool that calls `api.search.query` only for a search inside your plugin. One record per section of a long document. |
| `api.changeset.record(command=, kind=, ident=, facet=, before=, inverse=, slot=)` | Changes ledger entry; never raises. |
| `api.panel_state()` | → `{open, visible, since, panels}`: whether its panel is open and on screen. Panel code runs only while its tab is visible: a tool that needs it says "open the tab first". |
| `api.encode_image(path)` / `api.resolve_image(image)` | A local image → data URI; `resolve_image` passes http(s) and data URIs through and encodes local paths (for vision APIs). |
| `api.chat_reference(trigger=, id=, label=, href=, group="", description="")` | Add one `@` / `/` row to the chat box at runtime (fixed ones: `contributes.chat.references`). |
| `api.register_tts(synthesize)` / `api.register_tts_voices(list_voices)` | A voice provider: `synthesize(text, voice_id) -> {audio_base64, mime}`; `list_voices() -> [{id, label}]`, merged over `contributes.tts.voices` (keep it fast: it runs when a voice menu opens). |
| `api.register_llm_provider(…)` / `api.register_coding_agent(agent_id, factory, …)` / `api.register_ide_hookup(kind, label=, auto_apply=True)` | AI providers, coding agents and IDE hookups, only when asked (`plugin_examples_app`). |
| `api.provide_spaces(impl)` | Only the Ducky Account plugin: it offers the shared spaces every plugin syncs through. Never in yours. |
| `api.crypto.aead_seal(key, nonce, data, aad)` / `.aead_open(key, nonce, data, aad)` | AES-256-GCM from the app's own cryptography (32-byte key, 12-byte nonce, tag appended). Use it instead of importing `cryptography`, which the app may not bundle all of. A wrong key, nonce or `aad` raises `InvalidTag`. |
| `api.listener(command, params, timeout=None)` | One command to the live UEFN listener. |
| `api.http_json(method, url, …)`, `api.poll(…)` | HTTP JSON and polling helpers. |
| `api.emit_automation(trigger_id, payload)` | Run this PC's workflows that start on your trigger. |
| `api.emit_hook(hook_id, payload=None)` | Fire one of your `contributes.hooks`: the sound the user put on it plays. |
| `api.set_appearance_profile(profile_id)` | Switch Appearance to one of your own `appearance.profiles` (its `id`), as if picked in Settings. |
| `api.connection(fn, label=, program=)` | Header Connections row; `fn()` is cheap and returns `{online, detail}`. |
| `api.people(fn, label=)` | A section in the header's Online tab; `fn()` is cheap (cached, no network wait) and returns `[{id, name, detail, online}]`. Online people count toward the header badge. |
| `api.register_secret_test(secret_key, fn)` | Settings → Test for a key: `fn(api_key) -> {ok, detail}`. |
| `api.spotlight(window=, box=, title=, body=, steps=, click=, wait=)` | Highlight a control in UEFN, Blender or any window (same as `ducky_ui_show`). |
| `api.log(msg)`, `api.is_enabled()`, `api.plugin_id` | Logging, enabled check, your id. |

That table is the everyday set. `ducky_plugin_reference` ends with the live list of every
`api.*` call, read from the app itself (voices, coding agents, IDE hookups, image helpers,
`@` / `/` chat references, …): check it before guessing a call that isn't above.

Secrets: `from backend.agent.secrets import get_key` then `get_key("<secret id>")`.
Non-secret settings values: `from frontend.ui_web.plugin_host_api import
prefs_plugin_get` then `prefs_plugin_get(api.plugin_id).get("<property id>")`.
Push a live update to your panels: `from frontend.ui_web.verse_editor.panel_events
import push_agent_event` then `push_agent_event({"type": "<id>_changed", …})`; the
panel subscribes with `plugin.subscribe`.

Long work (a download, an install, a big job) gets a row in the header's activity list:
`from frontend.ui_web.agent_modes import push_ui_event` then
`push_ui_event({"type": "background_job", "id": "plugin:<plugin id>:<job>", "source": "plugin",
"title": …, "detail": …, "percent": 0-100 or None, "phase": "working"|"done"|"error", "cancelable": True})`.
Its Cancel calls your panel RPC `cancel_job(job_id=<that id>)`: register one (plus its
`<id>_cancel_job` tool). When it ends, `from frontend.notifications import alert` and
`alert("<Plugin>", "Done.", level="ok"|"error")` for the Windows notification.

### Many agents at once

A team can call one plugin from many agents at the same time, and `register()` runs in
every Ducky process (the app, each agent host, each tool server). Five rules:

1. **Background work only in the app:** start threads, timers, servers, ports and
   subprocesses only `if api.is_app():` (or `if not api.is_app(): return` first).
   Validate warns about any that `register()` starts unguarded.
2. **No per-call state in module globals:** take it from the arguments or keep it in
   `api.data`; two calls run at the same time.
3. **Short tools:** under about 5 s of CPU per call. Longer work goes in a subprocess or a
   workflow node, so it never holds the tool server.
4. **One connection? Say so:** `"tool_concurrency": 1` in plugin.json runs one call at a
   time (default 8). A call that waits past 2 minutes answers "plugin busy".
5. **Small results:** return what the agent needs; Ducky spills big ones to a file.

### In the chat: a tool's card shows what it made

A call to your tool draws a card in the chat with your plugin's name and an **Open** button
for your panel (the person opens the tab when they want it; agents never pull it to the
front). Pictures, videos and sounds whose full paths are in your result play right on the
card, and pressing one opens it big. Nothing to do for that: return the file paths.

Say more with a `chat_view` object in the dict your tool returns:

```python
return {"ok": True, "path": out, "chat_view": {
    "title": "Trailer 2 · 0:15",                    # the card's caption
    "media": [out, cover],                             # files to show first, in order
    "blocks": [{"type": "key_value", "pairs": [{"key": "Clips", "value": "12"}]}],
    "open": {"panel": "main", "label": "Open in Media Kit"},  # which panel Open shows
}}
```

`blocks` are the chat's rich blocks (`paragraph`, `list`, `table`, `key_value`, `callout`,
`code`; `code` with `language: "chart"` and a chart spec draws a chart, which opens big
too). Media must be files on this PC (images, video, audio, PDF, SVG); web addresses are
not loaded. Don't open your tab from a tool to show a result: the card is the result.

### Make it findable

Agents find skills and tools with `skill_search` and `ducky_get_tools(pattern=…)`, which
rank by the words you write. Your skills and tools are indexed the moment the plugin is
installed; nothing to register. What decides whether they are found:

- Every `skills/*/SKILL.md` and `references/*.md` has a **one-line** `description:` that
  says what it covers and when to read it (validate fails without one; a `>-` block reads
  as empty). Add `keywords:` with the words people use.
- One topic per `##` section: search points an agent at a single section by its heading.
- Every `@api.tool` docstring **starts** with one line saying what it does and when to use
  it ("Split the clip under the playhead in two, for cutting a shot."). Validate warns
  under 30 characters.
- `ducky_plugin_test` ends with a **search** check: each skill file and tool searched by
  its own description. Anything outside the top 5 is listed with what beat it: reword it.

### Make the person's things findable (free)

When the person says "show me the promo" or "open my Dragon card", agents call
`ducky_find("promo")` first. It searches every enabled plugin's `api.data`, in the
person's Local copy and every team copy they can still open (never a lost team, never
`sensitive` docs), and answers which plugin and copy holds it and the tool that opens it.
Your plugin gets this with no code. What you do:

- **Name every doc a person would ask for:** a `name`, `title` or `label` field with the
  words they use ("UEFN Ducky Promo", not `p_80a9`). A doc without one is found only by its key.
- **Key docs as `<kind>.<id>`** (`project.80a9`, `card.pip`) and have a `<prefix>_<kind>_get`
  tool (`imk_project_get`): `ducky_find` names it as the hit's `open_with`.
- **Files keep their real names** (`exports/UEFN Ducky Promo.mp4`); `ducky_find` matches file names too.
- **Long text** (wiki pages, card rules): `api.search.upsert` one record per section; `ducky_find` merges them.
- **List tools take an optional `query=`** that filters by name, for a look inside one project.
- **Never write a cross-plugin or "search everything" tool:** `ducky_find` is that tool.

`ducky_plugin_test` warns about a doc prefix whose docs have no name.

### The panel bridge

Panels are sandboxed iframes that talk to the host with `postMessage` on channel
`uefn-plugin-ui` (helper in the example below).

| Method | Params → result |
|---|---|
| `plugin.call` | `{method, params}` → your RPC's return |
| `plugin.info` | → `{pluginId, panelId, version, filePath}` (`filePath` in file editors) |
| `plugin.subscribe` | `{types: [...]}`: host push events you want (`plugin_scope_changed` always comes from the host, with `reason`; subscribing to it adds nothing) |
| `theme.get` | → `{vars}` (keys without `--`; the kit applies them) |
| `prefs.get` / `prefs.set` | `{id}` / `{id, value}`: small per-plugin UI choices (follow the data scope) |
| `scope.get` | → `{scope: {kind, label, spaceId, readOnly}}` |
| `data.get` / `data.put` / `data.list` / `data.delete` | One JSON doc per key (≤ 1 MB) |
| `files.put` / `files.get` / `files.list` / `files.delete` | Base64 files (≤ 100 MB) |
| `cache.get` / `cache.set` / `cache.clear` | Throwaway cache |
| `files.pick` | `{accept?: "video"\|"audio"\|"image"\|"mesh"\|"pdf"\|"svg"\|"any", multiple?}` → `{files: [{kind, path, name, url}]}`; cancel → `{files: []}`. The PC's file picker. Big media stays where it is: pass `path` to your backend (ffprobe/ffmpeg read it in place) and play `url` (HTTP Range, seekable). `url` is "" for types a panel can't play (e.g. .avi): play a proxy instead. Never upload large files through `plugin.call` in chunks: a 7 GB video froze the app. A phone gets an error. |
| `files.pickFolder` | `{}` → `{folder}` (`""` when cancelled): the PC's folder picker, e.g. where exports go. A phone gets an error. |
| `files.dropped` | `{names?}` → `{files: [{path, name, kind, url}]}`: real paths of files just dropped from Explorer on the panel. A DOM `drop` only sees names: call this from your `drop` handler. |
| `ui.confirm` | `{message, title?, confirm?, cancel?, danger?}` → `{confirmed}`: Ducky's own "are you sure?" dialog. Never `window.confirm` or a bar inside the panel. |
| `ui.alert` | `{message, title?, ok?}` → `{ok}`: Ducky's own message dialog |
| `ui.pickModel` | `{purpose?: "chat"\|"video", current?, title?, extra?, hide?, anchor?}` → `{model, name}` (`model: ""` when cancelled): Ducky's own model picker. `extra`: your own rows `[{id, name, group, note?, vision?}]`; `hide`: gateway ids whose own models are left out (`["uefn_ducky"]`); `anchor`: your button's box, so the menu drops down from it |
| `models.list` | → `{default_model, models: [{id, name, provider, provider_key, is_local, supports_vision}]}`: the chat models `llm.complete` accepts |
| `llm.complete` | `{system, user, model?}` → text from the user's chosen model |
| `audio.recordStart` / `audio.recordStop` / `audio.recordCancel` | → `{id}`; `{id}` → `{b64, mime, seconds, bytes}`; `{id}`: record from the microphone in the host window (panels have no microphone) |
| `browser.open` / `browser.bounds` / `browser.navigate` / `browser.command` / `browser.state` / `browser.close` | A real Chromium pane pinned over part of your panel (sites that refuse iframes work): `{url}`; `{x, y, width, height, visible?}` in the panel's CSS px (send again on resize); `{url}`; `{command: "back"\|"forward"\|"reload"\|"stop"}`. `browser.ui_cover {cover}` hides it under your own popups. |
| `live.join` / `live.leave` / `live.state` / `live.send` | Live editing on a team copy: `{room?}`, `{topic, data, to?}`; pushes `live.state`, `live.message`, `live.peers`. Pointers, selections, press holds, follow and picture placeholders are the host's own script in every page (`live.pointer`, `live.select`, `live.hold`, `live.holds`, `live.view`, `live.nav`, `panel.error`): never call those. |
| `live.share` / `live.shared` | The group's value of one of your components (`{key, value}`: a player playing, the picked clip; null drops it), through the group's leader so every page ends on the same one, shown here at once; and all of them now (`{values: {key: {value, by}}}`); pushes `live.shared {key, value, by}`. Tabs, scrolling and the screen (`data-live-view`, `ducky-live-goto`) are shared by the host's script already; mark your own view controls `data-live-nav` to share their clicks. |
| `live.setMyState` / `live.memberStates` | This PC's own live state (`{state}`: an object ≤ 4 KB such as a camera or the tool in hand, null clears it; call it as often as it changes, latest wins) and everyone's now (`{states: [{from, state}]}`); pushes `live.memberState {from, state}` (null: they left). How a 3D view shows teammates moving around. |

Backend: `api.media_url(path)` → the same seekable link for any local media file (a proxy your
backend made, or a picked original); "" when a panel can't play that type. Loopback only.
Backend: `api.local_dir(name="")` → a plain folder on this PC for files you can rebuild (media proxies,
thumbnails, caches): not synced, not encrypted, erased with the plugin's data. Write a proxy there, then
play it with `api.media_url(path)`. Real records go in `api.data`.

Host pushes: `appearance_theme`, `panel.visibility` (`visible`),
`plugin_scope_changed` (`reason: "synced"`: merge the `keys` it lists, keep what is open;
`"switched"`: start over), and the types you subscribed to.
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
  "data_sharing": true,
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
