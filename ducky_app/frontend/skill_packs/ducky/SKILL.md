---
name: ducky
description: "UEFN-Ducky control panel — setup, IDE hookup, Skills studio, chats"
license: Ducky Source-Available License v1.0
metadata:
  label: UEFN Ducky
  version: 39
  managed_by: uefn-ducky
  author: UEFN-Ducky
  copyright: Copyright 2026 UEFN-Ducky
  allow_redistribute: true
---

# UEFN-Ducky — the app

UEFN-Ducky is a Windows control panel that bundles an MCP server. The panel
manages IDE hookup, chats, skills, and Verse files; the MCP server bridges AI
agents to a listener running inside the UEFN editor (port 4200).

This skill covers **using the app** — where things live, setup, and recovery.

Save/Yes popup locking MCP: Ducky presses Save on it automatically while a tool
waits; if the editor still reports busy, call `dismiss_uefn_modal` (Ducky host).

## Where things live

- **Skill packs:** `%LOCALAPPDATA%/UEFN-Ducky/skill_packs/<pack>/SKILL.md` +
  `references/` (standard Agent Skills folders). On Apply they deploy to
  `~/.claude/skills/`, `~/.cursor/skills/`, and each IDE's `<config dir>/skills/`.
- **Panel settings:** `%LOCALAPPDATA%/UEFN-Ducky/panel_settings.json`.
- No Ducky side-files go in the UEFN project except `.ducky/**` (tests, tasks)
  and Ducky's managed `Content/Python/init_unreal.py` (listener boot — auto
  written on project open). **Never delete that init.** Do not add any other
  `.py`. `execute_python` is in-memory only. Scratch / captures / memory →
  `%LOCALAPPDATA%/UEFN-Ducky/`.

## Loading skill references (hard rule)

When the UEFN MCP server is available, load pack detail with
`skill_read_subskill("<pack_id>", "<id>")` only.

**Never** use the IDE Read/open-file tool on `~/.claude/skills/**`,
`~/.cursor/skills/**`, or `references/*.md` under those trees — Cursor treats
them as outside the workspace, permission prompts fail, and the same content
is already served by MCP. After a Read error on those paths, do **not** retry
Read; call `skill_read_subskill` instead.

## Setup & IDE hookup

- **Listener:** open the UEFN project; the panel should read "Listener online"
  (port 4200). If the panel is closed, launch `UEFN-Ducky.exe`.
- **IDE hookup:** Settings → MCP → Apply merges the `uefn` MCP server into the
  IDE config (Cursor, Claude Desktop, Antigravity) and deploys every skill pack
  as a standard skill folder.
- **Skills studio, MCP plugins, per-chat toggles:** see the `panel_guide`
  reference.
- **Build your own desktop plugins:** see **Plugins** below.
- **Custom Verse templates** (single file or multi-file system packs in AppData):
  `skill_read_subskill("ducky", "verse_templates")` then `ducky_verse_template_*`.
  Never edit Store `verse_template_*` packs.
- **Open/close UEFN, private version, memory calculation:**
  `skill_read_subskill("ducky", "publish_private")`. Reopen the current island
  with `ducky_restart_uefn` (`-ValkyrieProject=`, then wait until connected).
  Private version is Project → **Upload to Private Version...**. Memory
  calculation is Project → **Launch Memory Calculation**, and only after the
  Launch Session menu is **Launch on this PC**. Copy the code, send it, then
  press **OK** on `Private version has been created!`.

## After an EXE update / reinstall / UEFN reopen

**Automatic** — no Settings → Apply needed:

1. **Launch the new EXE (or any IDE MCP reconnect)** → `ship_newest_everywhere` copies the listener to AppData, upgrades shipped skill packs into Cursor / Claude / Antigravity **and** AppData (used by the in-panel UEFN-Ducky agent), and rewrites each IDE's `uefn` MCP entry to this EXE. Old chats pick up new rules, skills, and tools on the next send.
2. **UEFN comes online while the panel is open** → same ship runs again (offline→online).

If UEFN was already open with an old in-memory listener, call `reload_listener` once so it loads the AppData copy just shipped. If still stuck, stay on `workspace_*` / `ducky_get_status`. Do not restart the editor to refresh a stale listener. Reopening the current island is `ducky_restart_uefn` (see `publish_private`).

### What auto-updates vs what is preserved

| Updates | Never overwritten |
|---------|-------------------|
| Shipped skill packs (`uefn`, `ducky`, …) when bundled version is newer | Custom / Skills-studio packs you created |
| IDE skill folders tagged `managed_by: uefn-ducky` | Your own skill folders (no managed tag) |
| `mcpServers.uefn` bridge command/args | Other MCP servers (`im-hungry`, etc.) |
| In-editor listener source in AppData | `panel_settings.json`, credentials, chats, custom duckies, personalities |

User skill edits that **bump the pack version above** the bundled version are kept.

## Other duckies, lanes and messages

Delegating, group swarms (no separate subagents), write lanes, changesets and
agent-to-agent messages: load `skill_read_subskill("ducky", "groups_and_lanes")`.
In short: reuse a seated member (`ducky_group_members` → `ducky_send_chat_message`)
before adding one (`ducky_spawn_chat(…, group_id=…)`), recycle a bloated member with
`ducky_recycle_member`, split parallel writes into lanes first, and never re-spawn
after a timeout: the reply arrives as a `[ducky:agent-message]` turn.

## Ask the user (inline questionnaire)

**HARD:** path forks / "Your call" / A–B–C / wait-vs-proceed → call
`ducky_ask_user(questions=[{id, prompt, options:[{id,label,description}]}])`.
Never dump those choices as plain chat text — a questionnaire docks above the
composer until answered. Floor tool (always in tools[]). Batch up to 8 questions
per call.

## Plans (outline tree)

Multi-step work uses `ducky_create_plan` — never a prose Fix plan. Field roles:
`overview` = short summary; `body_markdown` = description; `nodes` = JSON array.
Follow the app `PLAN_PROTOCOL` (Diagnose → Fix → Verify; tick
`ducky_plan_update_node`). Templates: `ducky_list_plan_templates` /
`ducky_instantiate_plan_template` (try `verify-loop`). Settings → Plans has
Templates | Project Plans. Plan mode: outline only. Agent mode: follow and
check off.

## Show the user (Show me)

When the user asks where something is or how to find or open it (a setting, a
plugin, a button, a workflow node), or you tell them what to click: show it with
`ducky_ui_show` (it takes them there, highlights it with a popup, and the chat
keeps a Show me button); several things in order go in one call's `steps` (Back /
Next in one popup). Load `skill_read_subskill("ducky", "guided_ui")` for the
routes and target ids (Settings tabs, Store plugins, Workflows, chat box).

## Workflows

Building, changing, explaining, running or organizing a workflow: load
`skill_read_subskill("ducky", "workflows")` first. It covers the editor, the
graph shape, every workflow tool, locks (never change a locked node or group
without the user's OK) and how to show the user on the canvas as you work
(`save_workflow` frames what changed; `show_workflow` points at parts with a
caption).
Image, 3D and character pipelines (prompt to picture to 3D to rig to animations
to UEFN) are workflow nodes too; their paid steps need the user's OK first.

## Plugins (extend the app yourself)

Any new tab, panel, dock, tool, workflow node, theme or other app feature is a
desktop plugin built with `ducky_plugin_*` only. **Always load
`skill_read_subskill("ducky", "ai_plugins")` first**: it has the path, the hard
rules and a complete plugin, and says which of these to load next:

| Reference | When |
|---|---|
| `plugin_look` | Any panel, dock, editor or theme: the UI kit, every Appearance variable, panel states |
| `plugin_data` | The plugin stores anything; Local or one team; `sensitive` |
| `plugin_publishing` | Sharing through the Store, team or public, licenses, what compiles |
| `plugin_testing` | After every change: validate, test, errors |
| `plugin_known_problems` | Before you say it's done |
| `plugin_examples_ui` | Dashboard tab, dock, file editor, theme |
| `plugin_examples_tools` | Tools only, workflow nodes and templates, settings and secrets, connection checks, team data |
| `plugin_examples_uefn` | Verse templates, UEFN listener tools, Blender and Meshy pipelines |
| `plugin_examples_app` | Image generators, AI providers, sounds and hooks, walkthroughs |

The rules every plugin follows: `ducky_plugin_list` first and never glob or shell
AppData; search `ducky_find_tools` and reuse tools with `api.call_tool`; an
`@api.tool()` and a workflow node for every user action; the UI kit and Appearance
variables only (never color literals); data only through `api.data`; validate, test
and read errors after every change; share only through the Store
(`ducky_plugin_build` → `ducky_plugin_publish`). Never git-clone a Store plugin,
never edit the EXE, never `ducky_skills_create_pack` unless they asked for a skill
pack.

For driving UEFN itself (devices, Verse, wiring), follow the **UEFN MCP** skill —
this pack is only about the app.
