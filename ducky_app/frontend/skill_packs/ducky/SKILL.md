---
name: ducky
description: "UEFN-Ducky control panel — setup, IDE hookup, Skills studio, chats"
license: Ducky Source-Available License v1.0
metadata:
  label: UEFN Ducky
  version: 25
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
- **Build your own desktop plugins** (themes, panels, tools, any contribution):
  `skill_read_subskill("ducky", "ai_plugins")` then use `ducky_plugin_*`.
- **Custom Verse templates** (single file or multi-file system packs in AppData):
  `skill_read_subskill("ducky", "verse_templates")` then `ducky_verse_template_*`.
  Never edit Store `verse_template_*` packs.

## After an EXE update / reinstall / UEFN reopen

**Automatic** — no Settings → Apply needed:

1. **Launch the new EXE (or any IDE MCP reconnect)** → `ship_newest_everywhere` copies the listener to AppData, upgrades shipped skill packs into Cursor / Claude / Antigravity **and** AppData (used by the in-panel UEFN-Ducky agent), and rewrites each IDE's `uefn` MCP entry to this EXE.
2. **UEFN comes online while the panel is open** → same ship runs again (offline→online).
3. Start a **new chat** so tool schemas refresh (prompt cache is per-chat).

If UEFN was already open with an old in-memory listener, call `reload_listener` once (or restart UEFN) so it loads the AppData copy just shipped.

### What auto-updates vs what is preserved

| Updates | Never overwritten |
|---------|-------------------|
| Shipped skill packs (`uefn`, `ducky`, …) when bundled version is newer | Custom / Skills-studio packs you created |
| IDE skill folders tagged `managed_by: uefn-ducky` | Your own skill folders (no managed tag) |
| `mcpServers.uefn` bridge command/args | Other MCP servers (`im-hungry`, etc.) |
| In-editor listener source in AppData | `panel_settings.json`, credentials, chats, custom duckies, personalities |

User skill edits that **bump the pack version above** the bundled version are kept.

## Delegating to another ducky

Each ducky is a saved profile (skills, tools, model, personality) with a
`when_to_use` hint.

**Reuse first, spawn second, recycle when bloated:**

1. `ducky_list_duckies` → pick the specialist by `when_to_use`.
2. `ducky_agent_list` → check `my_subagents`. If you already have that specialist
   for the same area of work, follow up with
   `ducky_send_chat_message(conv_id="…", message="…")` — do **not** spawn a
   duplicate.
3. Only when no relevant child exists:
   `ducky_spawn_chat(ducky="<id or name>", message="…")` (blocks; returns the
   reply by default).
4. If a child is still the right specialist but its context is too long or
   confused: `ducky_recycle_subagent(conv_id="…", continue_message="…")` — it
   writes a full handoff, archives, and a fresh twin continues from that
   handoff. Prefer this over opening a second parallel copy.

Delegate when a task clearly fits another ducky's specialty — not for every
small step. Omit `ducky` for a plain default sub-agent.

A ducky can also run on an external coding agent: `ducky_spawn_chat(...,
coding_agent="claude_code" | "codex" | "cursor")`, or automatically when the
profile's favorite model slot names one. The sub-agent keeps one upstream CLI
session per chat, so follow-ups remember everything.

## Agent-to-agent messaging

If a spawn times out it is NOT dead: the result arrives later in your chat as a
`[ducky:agent-message]` turn (correlated by `response_id`) — never re-spawn.

- `ducky_agent_list` — live agents (chats) you can message: id, backend, running.
- `ducky_agent_send(to=…, message=…, expect_reply=true)` — fire-and-forget; you
  get a `response_id`, then FINISH your turn. The reply (or an inactivity notice
  like `turn-ended` / `errored` / `awaiting-input`) arrives as a new
  `[ducky:agent-message]` turn. To answer someone, echo their `response_id` with
  `expect_reply=false`.
- `ducky_agent_inbox` — re-read your recent inter-agent messages in full.
- `ducky_agent_transcript(conv_id)` — read a peer chat's history.
- `ducky_agent_stop(conv_id, cascade=…)` — stop a runaway agent (+its children).

External coding agents must pass their own chat id as `sender=`/`conv_id=` (it is
in their system prompt); embedded duckies may omit it.

## Ask the user (inline questionnaire)

**HARD:** path forks / "Your call" / A–B–C / wait-vs-proceed → call
`ducky_ask_user(questions=[{id, prompt, options:[{id,label,description}]}])`.
Never dump those choices as plain chat text — a questionnaire docks above the
composer until answered. Floor tool (always in tools[]). Batch up to 8 questions
per call.

## Plans (outline tree)

Multi-step work uses **one plan per chat** with an outline of main → subplans
(not separate nested chat plans). **Always call the plan tools** — never leave only
a prose "Fix plan" / markdown checklist in chat.

- `ducky_create_plan(title, overview, body_markdown, nodes=[{id,content,status,children}])`
- `ducky_plan_add_node` / `ducky_plan_update_node` / `ducky_plan_delete_node` / `ducky_plan_move_node`
- `ducky_get_plan()` — plan + outline numbering (`1`, `1.1`, `1.1.1`, …)
- `ducky_list_plans` — **current project only**
- Templates (global, reusable): `ducky_list_plan_templates`, `ducky_create_plan_template`,
  `ducky_instantiate_plan_template` — instances are snapshots; edits never cross

**Field roles:** `overview` = short summary only; `body_markdown` = description;
`nodes` = JSON **array** argument (never paste nodes/XML into overview — empty nodes
→ UI "0 of 0 steps").

**Followable shape:** Diagnose → Fix → Verify; each leaf = one action + Done-when
(name the tool when known). Parents cannot complete while nested subplans are unfinished.
Work depth-first on open leaves. **CHECK OFF every step** — `in_progress` BEFORE
the work, `completed` when Done-when is met — with `ducky_plan_update_node`.
Re-check the plan every tool round. Mutators are blocked until a leaf is
`in_progress`. If findings flip the approach, update the tree before more
mutators — never thrash off-plan.

Settings → Plans has **Templates** | **Project Plans** tabs (like Skills | MCPs).

In **Plan** mode: create/update the outline only — do not modify the level. The
user reviews, then switches to **Agent** mode (or **Send to ducky**) to execute.
In **Agent** mode: create the plan after brief discovery if missing, then follow it.

## AI-made plugins (extend the app yourself)

Any duckie can author a desktop plugin into this install (shared drafts, not
per-AI). Load the full flow with:

`skill_read_subskill("ducky", "ai_plugins")`

Short path: `ducky_plugin_reference` → `scaffold` → `write_file` → `validate` →
`install` → user trusts once via Settings → Store → iterate. Never edit core
files — only contributions.

For driving UEFN itself (devices, Verse, wiring), follow the **UEFN MCP** skill —
this pack is only about the app.
