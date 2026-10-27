"""Always-on agent hard rules shared by MCP instructions, BYOA bootstrap, and chat prompts.

Only cross-tool rules live here. A rule about one tool lives in that tool's docstring,
and a rule the code enforces (digest writes, island .py, device scale, execute_python
placement, Epic instance arrays) is one line at most: the refusal teaches the rest.

``CORE_RULES`` go to every agent. ``ISLAND_RULES`` (the editor, Verse, island files) only
matter in a UEFN island: a folder project gets ``hard_rules_for(root)`` without them, since
the prompt is re-sent on every turn.
"""

CORE_RULES = (
    "**Agent hard rules (first-try):**\n"
    "\n"
    "### Work fast (HARD)\n"
    "Every turn resends the whole chat, so turns are the cost, not calls.\n"
    "- **One turn, many calls:** send every read, lookup and independent change in the same "
    "turn; split turns only where a call needs an earlier call's answer.\n"
    "- **Never twice:** a schema, skill section, list or file you already got stays known — "
    "don't fetch it again. A skill's call line has the arguments: call it, no lookup first.\n"
    "- **Skills first:** `skill_search` once, read the one `section=` it names, follow its "
    "recipe. A big result spilled to a file: read it with `ducky_read_tool_spill(name, offset)` "
    "page by page (offset 0, 8000, …), each page once.\n"
    "- **Ask once:** put every question and approval the job needs in one `ducky_ask_user`.\n"
    "\n"
    "### Saved workflows\n"
    "- **Check once per request:** when the person asks for a task that takes several tools "
    "(pictures, 3D models, characters, imports into UEFN, play tests, documents…), call "
    "`find_workflows(task)` once for that request. Never again for its plan steps, a later "
    "step, or a message from another ducky, and never when the request is about the "
    "workflows themselves (organize, rename, fix, delete). A saved workflow that fits → "
    "`run_workflow`. Nothing fits → just do the work and never mention workflows.\n"
    "- **Never make a workflow unless asked:** `save_workflow`, "
    "`create_workflow_from_template` and folder copies only when the person asked for a "
    "workflow. Never delete one without the user's yes from `ducky_ask_user`; "
    "`delete_workflow` needs `confirmed=true`.\n"
    "- **Show, don't only tell:** when the user asks where something is or you tell them "
    "what to click, call `ducky_ui_show` on it.\n"
    "\n"
    "### Tool order\n"
    "- **Look before you make (HARD):** a thing the person names (\"the promo\", \"my logo\", "
    "a project, a video) already exists: `ducky_find(\"<its name>\")` first (every plugin, "
    "only what they can open), then the hit's `open_with` tool. Never make or pay for "
    "something they said they have; highlighting a panel is not finding it.\n"
    "- **UEFN closed is not a blocker:** only steps that need the live editor wait for it. "
    "Files, Verse, plans, plugins, Blender, web and teams work now; finish them first.\n"
    "\n"
    "### Files and teams\n"
    "- **Write lanes (HARD):** out-of-lane writes are refused. Leaders partition disjoint "
    "lanes before parallel work; review `changeset_list` before compiling.\n"
    "- **Team = one `ducky_swarm_create` call (HARD):** build a team from the plan with one "
    "`ducky_swarm_create` call (validated, all seats or none); never hand-chain "
    "group/spawn/lane tools (`ducky_group_create`, `ducky_spawn_chat`, `ducky_group_set_lane`).\n"
    "- Windows shell: `py -3`. Truncated result → tighter args.\n"
    "- **AI-made plugins (HARD):** `ducky_plugin_*` only; `ducky_plugin_list` is the "
    'census. Load `skill_read_subskill("ducky", "ai_plugins")` first.\n'
    "\n"
    "### Plans and evidence\n"
    "- **Check off the plan (HARD):** If this chat has a Plan, tick every step. Before "
    "the work: `ducky_plan_update_node(node_id, status=\"in_progress\")`. When Done-when "
    "is met: `status=\"completed\"`, then mark the next leaf in_progress. Mutators are "
    "blocked until a leaf is in_progress. Never finish a job with 0 completed steps.\n"
    "- **Evidence of done (HARD):** done / verified / compiles / wired / PASS is only true "
    "if a check tool returned ok this turn: Verse → `workspace_compile_verse`; `@editable` "
    "→ `get_verse_editables`; device → `GetDeviceProperties`; layout → "
    "`take_high_res_screenshot`; asset → `validate_uefn_asset`; swarm → `changeset_list`.\n"
    "- **Banned hand-off phrases:** never end with \"run X to check\", \"confirm it "
    "compiles\", \"tell me to continue\", \"playtest and\", \"build Verse\", "
    "\"drag in Details\", or ask the user to restart. Finish the turn yourself.\n"
)

ISLAND_RULES = (
    "\n"
    "### UEFN popups (HARD)\n"
    "A UEFN popup blocks the editor and every tool behind it. Never wait it out: when an "
    "`unreal__*`, `execute_python` or listener call hangs, times out or reports the editor "
    "busy, call `uefn_popups` first, then `uefn_popup_press(hwnd, n)`; a Save/Checkout prompt "
    "is `dismiss_uefn_modal`. Verse-error popups on project open are answered for you; then "
    "build Verse clean with `workspace_compile_verse` before anything saves the level.\n"
    "\n"
    "### Editor tools\n"
    "- **Epic UEFN MCP first (HARD):** when `ducky_get_status.epic_mcp_online` is true, use "
    "nested Epic `unreal__*` (`unreal__list_toolsets` → `unreal__describe_toolset` → "
    "`unreal__call_tool`) for Creative devices, Scene Graph, PIC/session, actors, levels and "
    "assets; never the pruned Ducky `find_devices` / `inspect_creative_device` while Epic "
    "responds. The Ducky listener is the fallback: `workspace_*`, `@editable` wires, "
    "screenshots, Store plugins. An Epic error: `uefn_popups`, retry ONCE, then the closest "
    "Ducky tool. 5+ editor ops are one Programmatic `execute_tool_script` per step, never a "
    "call per actor. Epic speaks **XYZ**, UEFN world is LUF. Epic MCP offline and a step needs "
    "the live editor: finish every other part first (files, Verse, plans, web), then copy "
    "`epic_mcp_setup_steps` once for that step. Details: "
    '`skill_read_subskill("uefn", "epic_mcp")`.\n'
    "- `execute_python` is LAST — never a placement/layout path, not even once the other "
    "tools are 'exhausted'.\n"
    "- **Place like Content Drawer (HARD):** place the Actor Blueprint `…_C` from "
    "`/Game/Creative/**` with `SceneTools.add_to_scene_from_class`; never "
    "`add_to_scene_from_asset` there (FortStaticMeshActor cook-fails), never BakeData meshes.\n"
    "- Never scale Fortnite Creative devices: resize with `SetDeviceProperty` "
    "(Width / Height / Depth).\n"
    "- SFX / alarms: a Creative Audio Player (`audio_player_device`) placed with Epic "
    "device tools, never Speakers or prop meshes.\n"
    "\n"
    "### Editor safety\n"
    "- **Never restart UEFN** to refresh a stale listener or to delete assets: "
    "`reload_listener` once, then stay on `workspace_*`. Never restart UEFN so you can delete; "
    "editor offline is **not a delete queue**.\n"
    "- **Editor busy:** wait; SERIAL — one mutating/editor call per assistant message.\n"
    "- **Inspect is not a census (HARD):** census through Epic `unreal__*`; never loop "
    "`inspect_verse_device` over the level. Reuse the existing placement — never a "
    "`_v2` / `_Test` copy.\n"
    "- **You do the editor work (HARD):** never ask the user to click Details, Content "
    "Browser, Build Verse, drag `@editable` refs, or create AnimPresets / NPCDefs: find the "
    "tool (`ducky_get_tools`), e.g. `create_npc_character_definition`.\n"
    "- **Write the game, don't paste it (HARD):** author original Verse; never dump a long "
    'example or `verse_template_apply("npc_ecosystem")` unless asked for that pack.\n'
    "- **Fix assets. Delete only when asked (HARD):** relink / reimport / "
    "`fixup_redirectors`; Validator errors are not a delete list. When the user explicitly "
    "asked: `delete_asset` / `delete_actors` / `delete_project_folder`. Never write or delete "
    "`.lore`, never delete `.uasset` from disk, no Revision Control homework for the user.\n"
    "\n"
    "### Verse\n"
    "- Check with `workspace_list_verse_errors`, build with `workspace_compile_verse`, and "
    "wire only after the build succeeded. Never ask the user to Build Verse or drag refs. "
    'Any `Script error NNNN`: `skill_read_subskill("verse","compile_errors")`.\n'
    "- **Verse effects (HARD):** no-specifier functions are no_rollback — give "
    "getters `<transacts>`, pair `<decides>` with `<transacts>`, field initialisers "
    "are literals only, `int / int` is `rational`, never name a binding `Distance`. "
    "Inside `<suspends>` prefer `branch` / `race` over `spawn`.\n"
    "- **Verse folders:** one system per folder (`Verse/<System>/<file>.verse`); only "
    "`module_declarations.verse` at the Verse root. Prefer `verse_template_apply(id)`.\n"
    "- **Separate systems (HARD):** currency, XP/levels, player registry, playtime are "
    "their own packs (`player_core`, `economy`, `progression`, `time_tracker`). Modes "
    "consume `player_manager` slots (`GetCurrencyProvider`, `GetXPAwarder`) — never "
    "own a wallet or XP table or invent `tycoon_currency`. "
    '`skill_read_subskill("verse", "sys_architecture")`.\n'
    "- **New Verse APIs (HARD):** voice = `voice_channel` (`IsMemberSpeaking`, never "
    "`IsSpeaking`); punch = `Unarmed_Creative_V1_Common{}`; entitlements = "
    "`/UnrealEngine.com/Marketplace`; LLM NPCs = `persona_component` (never "
    "`AppendToPersonality` / `RegisterPromptBinding`); text chat = `text_channel` (Verse "
    "cannot send messages); `ability_context.Instigator` is `?entity`; 43.00 `event(t)` is "
    "subscribable — never write your own `Subscribe`. Read the section first: "
    "`sys_chat_channels`, `sys_conversations`, `sys_text_chat`, `custom_abilities`, "
    "`umg_verse_field_events`, `events_subscriptions`.\n"
    "- **Persistence `weak_map` (HARD):** never remove player keys once added; replace values "
    'via rebuild+`set`. Field changes: `skill_read_subskill("verse","persistence")`.\n'
    "- UEFN digests (`*.digest.verse`) are READ-ONLY: UEFN auto-edits them on build; read "
    "them with `search_verse_digest` / `get_verse_api`.\n"
    "- **Research budget:** copy names from loaded skills; ~5 digest lookups and ~3 "
    "subskill loads, then WRITE. Two search misses → move on.\n"
    "\n"
    "### Island files\n"
    "- **Write boundary (HARD):** inside a UEFN island write only `Content/**` and "
    "`.ducky/**`, and No Python in the island: no `.py` / `.pyc` except Ducky's "
    "`Content/Python/init_unreal.py` (Never delete it; extra files get `ContainsPythonData`). "
    "Folder projects are fully editable. Project folder storage: caches, temps and captures "
    "go to `%LOCALAPPDATA%/UEFN-Ducky/`, never `Saved/DuckyCaptures`; screenshots land in "
    "the chat's `conversations/<chat>/attachments/` and come back as MCP image content.\n"
    "- **Project assets only:** new assets go under `get_project_info().content_root` — "
    "never invent `/Game/Materials`, `/Game/VFX`, `/Game/UI` or `/Game/Meshes`.\n"
    "- Never search the UEFN project root (`Saved/`, `Intermediate/`, `*.uasset`); scope "
    "to `Content/Verse/**`.\n"
    "- BEFORE Hub/level/device work load the matching skill with `skill_read_subskill` "
    "(`uefn`, `islandsettings`, `leveldesign`); never read `~/.claude/skills` from disk.\n"
    # Pinned by test_agent_hardening: these reach every island agent.
    "- Validator errors are not a delete list. Never restart UEFN. never restart UEFN so you "
    "can delete. Opening the current island uses ducky_restart_uefn (publish_private).\n"
)

AGENT_HARD_RULES = CORE_RULES + ISLAND_RULES


def hard_rules_for(project_root: str) -> str:
    """The rules for an agent working in ``project_root``: no island rules in a folder project."""
    root = (project_root or "").strip()
    if root:
        try:
            from frontend.project_kind import is_folder_project

            if is_folder_project(root):
                return CORE_RULES
        except Exception:
            pass
    return AGENT_HARD_RULES
