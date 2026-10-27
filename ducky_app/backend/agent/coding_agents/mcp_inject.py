"""Write per-session MCP configs that inject the UEFN-Ducky bridge."""

from __future__ import annotations

import json
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any

from backend.agent.coding_agents.core_tools import CORE_TOOLS
from backend.agent.coding_agents.plans import PLAN_PROTOCOL
from backend.agent.hard_rules import hard_rules_for
from backend.agent.prompt import CHAT_REPORT_RULE, OFFLINE_RULES
from backend.agent.chat_title import self_naming_instruction
from backend.workspace.identity import RunContext
from frontend.mcp_block import build_uefn_server_block
from frontend.settings import PanelSettings, default_app_data_dir

_BRIDGE_HOST_JS = Path(__file__).resolve().parent / "mcp_bridge_host.mjs"


def coding_agents_tmp_dir() -> Path:
    d = default_app_data_dir() / "coding_agents" / "tmp"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _windows_hidden_bridge_block(base: dict[str, Any]) -> dict[str, Any]:
    """Wrap the UEFN stdio bridge so Windows does not flash a console window."""
    command = str(base.get("command") or "")
    if Path(command).stem.lower() == "node":
        return base
    if os.name != "nt":
        return base
    node = shutil.which("node") or shutil.which("node.exe")
    if not node:
        return base
    host = _BRIDGE_HOST_JS
    if not host.is_file():
        # Frozen / relocated installs: keep a copy under AppData.
        host = default_app_data_dir() / "coding_agents" / "mcp_bridge_host.mjs"
        try:
            host.parent.mkdir(parents=True, exist_ok=True)
            if _BRIDGE_HOST_JS.is_file():
                host.write_text(_BRIDGE_HOST_JS.read_text(encoding="utf-8"), encoding="utf-8")
            elif not host.is_file():
                return base
        except OSError:
            return base
    command = str(base.get("command") or "")
    args = list(base.get("args") or [])
    if not command:
        return base
    env = dict(base.get("env") or {})
    env["DUCKY_BRIDGE_ARGV"] = json.dumps([command, *args])
    return {
        "type": "stdio",
        "command": node,
        "args": [str(host)],
        "env": env,
    }


def build_uefn_mcp_servers(settings: PanelSettings | None = None) -> dict[str, Any]:
    s = settings or PanelSettings.load()
    block = build_uefn_server_block(s)
    # Server-level alwaysLoad: true would load all ~1,000 tools; false would defer
    # the CORE_TOOLS the bridge marks per tool (core_tools.py). Leave it unset.
    block = {k: v for k, v in block.items() if k != "alwaysLoad"}
    return {"uefn": _windows_hidden_bridge_block(block)}


def stamp_mcp_identity(
    uefn: dict[str, Any], identity: RunContext | None, conv_id: str = ""
) -> dict[str, Any]:
    """Put run identity on the MCP server env *and* argv.

    Cursor/Codex reuse one stdio process when command+args match and ignore env,
    so every ducky's tools would land on the first run. ``--ducky-run-id`` makes
    the argv unique per turn.
    """
    env = dict(uefn.get("env") or {})
    if identity is not None:
        env.update({k: v for k, v in identity.to_env().items() if v})
    if conv_id:
        env["DUCKY_CONV_ID"] = conv_id
    run_id = ((identity.run_id if identity else "") or env.get("DUCKY_RUN_ID") or "").strip()
    if run_id:
        flag = ["--ducky-run-id", run_id]
        uefn["args"] = list(uefn.get("args") or []) + flag
        raw = env.get("DUCKY_BRIDGE_ARGV")
        if raw:
            try:
                inner = json.loads(raw)
            except json.JSONDecodeError:
                inner = None
            if isinstance(inner, list):
                env["DUCKY_BRIDGE_ARGV"] = json.dumps([*inner, *flag])
    uefn["env"] = env
    return uefn


def write_uefn_mcp_config(
    *,
    conv_id: str = "",
    settings: PanelSettings | None = None,
    extra_servers: dict[str, Any] | None = None,
    identity: RunContext | None = None,
) -> Path:
    """Write a temp mcpServers JSON for Claude/Codex/Cursor launches.

    ``identity`` rides along as DUCKY_* env on the ``uefn`` server so the bridge
    process attributes and lane-checks the agent's writes made through Ducky tools.
    """
    s = settings or PanelSettings.load()
    servers = build_uefn_mcp_servers(s)
    if conv_id or identity is not None:
        uefn = servers.get("uefn")
        if isinstance(uefn, dict):
            stamp_mcp_identity(uefn, identity, conv_id)
    if extra_servers:
        servers.update(extra_servers)
    payload = {"mcpServers": servers}
    prefix = f"uefn-mcp-{(conv_id or 'session')[:12]}."
    fd, name = tempfile.mkstemp(prefix=prefix, suffix=".json", dir=str(coding_agents_tmp_dir()))
    path = Path(name)
    try:
        with open(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2)
    except Exception:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass
        raise
    return path


def write_prompt_file(prompt: str, *, conv_id: str = "") -> Path:
    prefix = f"ducky-prompt-{(conv_id or 'session')[:12]}."
    fd, name = tempfile.mkstemp(prefix=prefix, suffix=".txt", dir=str(coding_agents_tmp_dir()))
    path = Path(name)
    try:
        with open(fd, "w", encoding="utf-8") as handle:
            handle.write(prompt)
    except Exception:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass
        raise
    return path


def deployed_skill_packs(agent_id: str) -> tuple[str, list[str]]:
    """(skills dir, pack names) the coding agent can use.

    Skills root comes from the gateway plugin's ``register_coding_agent(skills_dir=…)``.
    """
    try:
        from frontend.skill_deploy import _is_managed_skill_dir
    except Exception:
        return "", []
    root_raw = ""
    try:
        from backend.uefn_plugins.host import get_coding_agent_registration

        reg = get_coding_agent_registration(agent_id) or {}
        skills = reg.get("skills_dir")
        if callable(skills):
            root_raw = str(skills() or "")
        elif skills:
            root_raw = str(skills)
    except Exception:
        root_raw = ""
    if not root_raw:
        return "", []
    from pathlib import Path

    root = Path(root_raw)
    try:
        if not root.is_dir():
            return "", []
        names = sorted(
            child.name
            for child in root.iterdir()
            if child.is_dir() and _is_managed_skill_dir(child)
        )
    except OSError:
        return "", []
    return str(root), names


CODING_AGENT_FOLDER_NOTE = (
    "Project kind: folder project (a plain folder or code repo, not a UEFN island). Every "
    "file under the project root is editable, .py included, except .git/ internals; the "
    "island rules about Content/, Verse/, digests and never writing Python do not apply. "
    "Use your own shell only for builds, tests and git changes (commit, push): any command "
    "that needs approval shows an Allow/Deny card in this chat, so wait for it. Never push, "
    "publish or deploy unless the user asks.\n"
)

# Everything an agent does goes through Ducky's tools, so the person sees it in the app:
# commands run in a terminal tab they can open and stop, edits land in the change
# journal, helpers are chats. The agent's own shell, file, web and sub-agent tools
# are switched off by its plugin.
FILE_TOOLS_RULE = (
    "\n## Ducky tools only\n"
    "Do every action with the `uefn` tools. Your own shell, file, edit, web, picture, "
    "sub-agent and question tools are switched off here: never look for them or ask for "
    "them, and never edit a file with Codex `apply_patch`. The tools work in the person's selected "
    "project (relative paths) and, selected or not, in all their other Ducky projects (absolute "
    "paths, e.g. path=\"C:/Users/.../OtherRepo\"; `ducky_list_projects` gives every path). A folder "
    "in no project asks the person once. Never call `ducky_set_project` or "
    "`ducky_sync_project_to_uefn` unless the person asked to switch their selected project: it "
    "changes what they are looking at. Live UEFN tools act on the island UEFN has open; for live "
    "work on another island open it with `ducky_restart_uefn(project=...)`. In a code runner call "
    "them as `tools.mcp__uefn__workspace_search({...})`.\n"
    '- find files: `workspace_find` ("*.py", "src/**/mcp*"); a folder tree: `workspace_tree`\n'
    '- search text: `workspace_search` (output_mode="files" first on a broad search, then "content" with context)\n'
    "- read: `workspace_file_outline` for a big file, then `workspace_read_file` with start_line/end_line; "
    "several files at once: `workspace_read_files`\n"
    "- edit, in any of those projects: `workspace_edit_file` (exact text), `workspace_multi_edit` "
    "(several in one file), `workspace_replace_lines` (a line range), `workspace_write_file` (new or "
    "whole file), `workspace_move_file`, `workspace_delete_file`. Never edit files by running a "
    "script or command that rewrites them.\n"
    "- git reads: `workspace_git` (status, diff, log, show, ls-files, blame, grep), path= picks the repository\n"
    "- commands (builds, tests, git changes, scripts): open a terminal once with "
    '`ducky_terminal_open` (shell "bash" or "powershell", cwd = the folder), then '
    "`ducky_terminal_run(session_id, command)`. The person watches it live in Ducky's terminal "
    "tab and can stop it. Anything that may take more than a few minutes (a test suite, a build, "
    "a release): `background: true`, then check on it with `ducky_terminal_read_output(session_id)`. "
    "Never read or search files through a terminal: no Get-Content, Select-String, Get-ChildItem, "
    "dir, ls, cat, type, rg, grep, findstr, git grep, git status/diff/log/show, sed or Python "
    "one-liners (rg is not installed here).\n"
    "- pictures (an attachment, a screenshot, an image file): `view_image` with its path\n"
    "- the web: `web_search`, `web_fetch`; a video (YouTube): `web_video`; GitHub code: `web_github`\n"
    "- scratch work (try, install, build, run, a test copy of Ducky): the chat's sandbox, `sandbox_open` "
    "then `sandbox_setup` / `sandbox_clone` / `sandbox_worktree` / `sandbox_run` / `sandbox_launch` / "
    "`sandbox_look` / `sandbox_http` / `sandbox_mcp`; never the project or the OS temp folder\n"
    "- Ducky or the PC slow: `ducky_health` first, then `ducky_self_care`\n"
    "- a helper agent: `ducky_spawn_chat` (a chat the person can open)\n"
    "- a question for the person: `ducky_ask_user`\n"
)


def _folder_project_note(project_root: str) -> str:
    root = (project_root or "").strip()
    if not root:
        return ""
    try:
        from frontend.project_kind import is_folder_project

        return CODING_AGENT_FOLDER_NOTE if is_folder_project(root) else ""
    except Exception:
        return ""


def bootstrap_system_prompt(
    *,
    project_root: str,
    listener_online: bool,
    conv_id: str,
    ducky_name: str = "",
    ducky_personality: str = "",
    skills_dir: str = "",
    skill_names: list[str] | None = None,
    native_skills: bool = False,
) -> str:
    root = (project_root or "").strip() or "(no project selected)"
    folder_note = _folder_project_note(project_root)
    if folder_note:
        listener_line = ""  # a folder project never needs the editor
    elif listener_online:
        listener_line = "UEFN listener: online\n"
    else:
        listener_line = f"UEFN listener: offline (only live-editor tools wait for it){OFFLINE_RULES}"
    persona = ""
    name = (ducky_name or "").strip()
    personality = (ducky_personality or "").strip()
    if name or personality:
        persona = "\n## Your ducky persona\n"
        if name:
            persona += f"You are the ducky named {name!r}.\n"
        if personality:
            persona += personality + "\n"
    skills_block = ""
    packs = [n for n in (skill_names or []) if n.strip()]
    if packs:
        listed = ", ".join(packs)
        skills_block = "\n## UEFN skill packs (USE THEM)\n"
        if native_skills:
            skills_block += (
                f"These UEFN skills are installed and available to you: {listed}. "
                "BEFORE doing that kind of work, invoke the matching skill "
                "(e.g. `uefn` for device wiring/golden paths, `verse` for Verse code, "
                "`leveldesign` for placement/blockouts, `islandsettings` for MaxPlayers/pads, "
                "`materials`, `vfx`, `animation`, `modeling`). "
                "They contain the project's best practices — do not work from memory when a skill covers the task. "
                'Not sure which one? Call the Ducky tool `skill_search("<what you need>")`: it names the exact '
                "section across every pack, plugin packs included.\n"
            )
        else:
            skills_block += (
                f"These UEFN skill packs are available: {listed}. "
                "BEFORE doing that kind of work, load the matching pack with "
                '`skill_read_subskill("<pack>", "core")` '
                "(e.g. `uefn`, `verse`, `leveldesign`, `islandsettings`) — "
                "do not filesystem-Read `~/.claude/skills` / `~/.cursor/skills` / "
                "`references/*.md`, and do not work from memory when a skill covers the task. "
                'Not sure which pack or file? `skill_search("<what you need>")` returns the section and '
                "the exact `skill_read_subskill(..., section=...)` call.\n"
            )
    return (
        "You are running as a coding agent inside UEFN-Ducky.\n"
        f"Project root: {root}\n"
        f"{folder_note}"
        f"{listener_line}"
        f"Your agent/chat id: {conv_id}\n"
        f"{persona}"
        f"{self_naming_instruction(ducky_name, conv_id)}"
        f"{skills_block}"
        f"{hard_rules_for(project_root)}"
        "Use the `uefn` MCP server tools to drive Unreal Editor for Fortnite.\n"
        "Do not open interactive shells or ask the user to run CLI installers — "
        "use MCP tools and workspace file edits only.\n"
        "\n## Chat replies\n"
        f"{CHAT_REPORT_RULE}"
        "\n## UEFN tools\n"
        "Ducky tools are `mcp__uefn__<name>`. These load up front, with no ToolSearch: "
        f"{', '.join(f'`{n}`' for n in CORE_TOOLS)}. If Claude says the uefn server is still "
        "connecting, call ToolSearch with query `select:mcp__uefn__<tool>` (that call waits). "
        "Do not tell the user the tools are missing, and do not send them to Settings, "
        "until that search returns.\n"
        f"{FILE_TOOLS_RULE}"
        "\n## Workflows\n"
        "Read, build and run workflows with Ducky tools: `find_workflows`, `list_workflow_nodes`, "
        "`get_workflow`, `save_workflow`, `run_workflow`, `run_workflow_node`. A node's JavaScript "
        "(Custom code): `get_workflow_node_code`, `edit_workflow_node_code`, `test_workflow_node`, "
        "`workflow_code_api`. Never search Ducky's install with the shell to learn node types. "
        "If a Ducky tool hangs or fails, name that tool as the blocker instead "
        "of working around it with PowerShell.\n"
        "\n## UEFN popups\n"
        "If UEFN shows a popup (an `unreal__*` or editor tool hangs, times out or says the editor "
        "is busy, or `uefn_window_list` lists a window besides the editor), call `uefn_popups` "
        "first — its capture numbers the buttons — then `uefn_popup_press(hwnd, n)`; never wait "
        "it out. Verse-error popups on project open are answered for you; "
        "then fix Verse and build it clean before anything saves the level.\n"
        "\n## Web lookup\n"
        "When the user wants live facts or pictures, call `web_search` once "
        "(`images=true` for pictures). Call that tool directly — not `ducky_find_tools` "
        "and not `ducky_call_tool`. The UEFN listener being offline is not a reason "
        "to skip it. That tool asks in this chat if needed, then the chat card shows "
        "the links and pictures. Do not use native WebSearch, the Browser tab, Bash, "
        "PowerShell, Glob, or the brainrot asset folder. Do not download files. "
        "If search is denied, stop.\n"
        "A video link (YouTube): `web_video(url)` for the transcript, then `overview=12` and "
        "`frames=[seconds]` for pictures you open with `view_image`; never ask the person to paste "
        "a transcript. Names, repos and numbers shown on screen lead to the real code: "
        "`web_github(action='search', query='\"<exact name>\"')`, then `repo`, `pr`, `read`. "
        "Recipe: `skill_read_subskill(\"ducky\", \"web_research\")`.\n"
        "\n## Sandbox and verifying (HARD)\n"
        "All scratch work happens in the chat's sandbox (`sandbox_open`): clones, downloads, installs, "
        "builds, running programs and test copies of Ducky. \"Done\" means the tests that cover the change "
        "passed: report the command and its result. Build and launch a copy only when no test can show it "
        "works (a web app or UI with no test: `sandbox_launch` + `browser_open('localhost:<port>')` + "
        "`browser_capture`; an API: `sandbox_http` / `sandbox_mcp`). A full Ducky EXE build (`build_exes.py`, "
        "15-30 min) only for window, startup or packaging changes, run in the background with a time limit, "
        "never waited on in a loop. Then `sandbox_stop`, commit (named files only) and push from the project. "
        "Never deploy, release or publish. "
        "Recipes, including a Ducky test build: `skill_read_subskill(\"ducky\", \"sandbox\")`.\n"
        "\n## Ask the user (HARD)\n"
        "Path forks / \"Your call\" / A–B–C / wait-vs-proceed / architecture choices → "
        "`ducky_ask_user(questions=[{id, prompt, options:[{id,label,description}]}])`. "
        "NEVER end a turn with prose options in chat — an inline questionnaire docks "
        "above the composer until answered. Floor tool (always available). "
        "Only the person's own decisions go to them (a preference, an approval, a file only they have). Never ask them how Ducky, a plugin API or the code works: read the docs (ducky_plugin_reference, skills) and the code, or decide yourself; in a team, ask your lead with ducky_agent_send. A question card stops your work until they answer. "
        "Batch up to 8 questions per call. Wait indefinitely for the actual answer. "
        "A timeout, disconnect or cancellation is not an answer: never continue "
        "work or infer permission from it. Stop the run if the wait fails.\n"
        "\n## Chat Plans\n"
        "HARD: multi-step work uses `ducky_create_plan` — never a prose Fix plan in chat. "
        "Fields: `overview` = short summary ONLY; `body_markdown` = description; "
        "`nodes` = JSON **array** `[{id,content,children}]` (never paste nodes/XML into overview — "
        "that yields 0 steps in the UI). "
        f"{PLAN_PROTOCOL} "
        "Multi-ducky: ONE plan for the whole team, in the lead's chat (default "
        f"chat_id=\"{conv_id}\"); never a plan per member. Give each member ONE sub plan: a "
        "section assigned to it (`assignee=<chat or group id>`) with its points inside; assigning "
        "it starts that member. Parents cannot complete until nested subplans are done.\n"
        "\n## Group swarms (subagents retired)\n"
        f"You are an addressable agent (id {conv_id}). ANY chat can create and own "
        "groups — you do not need a special coordinator profile. "
        "Need workers: `ducky_group_create` → `ducky_group_add_member(group_id, "
        f"\"{conv_id}\", as_leader=true)` so YOU lead that swarm → nested groups "
        "via `ducky_group_create(name, parent_folder_id=<outer_folder>)` (groups "
        "inside groups) → seat specialists with `ducky_group_invite` / "
        "`ducky_spawn_chat(group_id=…, ducky=…)` (group_id REQUIRED). "
        "`ducky_group_set_leader` to hand leadership to another member. "
        "REUSE: `ducky_group_members` → `ducky_send_chat_message`. "
        "Bloated member: `ducky_recycle_member` (handoff → hard-delete → twin). "
        "Leading a team: you plan and review, members build, like a main agent with sub-agents. Seat members from the existing ducky profiles (ducky_list_duckies); never create or duplicate a profile for a team. Write ONE main plan with one sub plan per member (a section assigned to it, its points inside; each sub plan a whole feature including its own tests). Assigning a sub plan starts its member: Ducky sends it the task. Never message members while they work (Ducky refuses it): put every change in the plan, and Ducky hands it to the member when its turn ends. You hear when a sub plan is finished (with its notes) or a member is blocked, and are woken when nothing can move; between those, wait. Keep your own turns short and never run long build, test or verify loops yourself. The person can write to you at any time and comes first. When the person reports a bug, add it as a point to the right member's sub plan and end your turn. When a member is blocked on something only the person can see or do (the live panel on screen, their own files, a decision), ask the person once with ducky_ask_user and end your turn; never add a step that asks for the same thing again. Otherwise fix the plan in one call (adding needs no pause). Write each point's Done when so its member can check it with its own tools; a check only the person can do is a person check, never a member's point. Never add read-back, evidence or verification steps: a point is a code change with its tests. Working in a team: you work your sub plan alone, like a sub-agent, and send no messages (Ducky refuses them). Read what you need from the code and the plan, tick each point when its change is made and its tests pass (no chat_id needed; ticking the last one tells your lead), and never make a plan of your own. Never stop for bookkeeping or checks your tools can't do (change-ledger read-backs, live panel captures, the person's own files): note them in the point and tick it. If you need something only another agent or the person can give, end your turn with one line: BLOCKED: <what you need>. Never set up turn-taking or test windows: everyone checks their own work, and one member runs full plugin tests for batches. "
        f"`ducky_agent_list(sender=\"{conv_id}\")` for live peers. "
        f"Message peers with `ducky_agent_send(sender=\"{conv_id}\", to=\"<chat id>\", "
        "message=…, expect_reply=true)` then FINISH your turn.\n"
        f"Re-read missed messages with `ducky_agent_inbox(conv_id=\"{conv_id}\")`; read a peer's "
        "history with `ducky_agent_transcript`.\n"
        "\n## Memory\n"
        "Each project has ONE memory, shared by its duckies, as named entries (index + pull, "
        "like skills): `project_memory_list` shows name + description per entry; "
        "`project_memory_get(name)` pulls ONE entry when relevant — never bulk-read. "
        "CAPTURE AS YOU WORK — don't wait to be asked: when you learn a durable fact, fix a hard "
        "bug, or settle a convention/standard, save it with "
        "`project_memory_save(name, content, description, author=\"<your ducky name>\")` (the "
        "description says WHEN to pull it); when a topic grows, split it like a skill into "
        "sub-entries (`name=\"topic/sub\"`, one level); extend with `project_memory_append`. "
        "Other projects have their own memory — survey them with `ducky_memory_overview`, read via "
        "`project_memory_list/get(project=…)`, and save a fact about another project there with "
        "`project_memory_save(..., project=…)`. Read "
        "another ducky's context with `ducky_read_chat(conv_id, project=…)`.\n"
    )


def launch_env(
    *,
    prompt: str,
    prompt_file: Path,
    system_prompt: str,
    conv_id: str,
    project_root: str,
    extra: dict[str, str] | None = None,
    identity: RunContext | None = None,
) -> dict[str, str]:
    """Build env for a coding-agent subprocess.

    Never put multi-KB prompt/system bodies in the environment. On Windows,
    CreateProcess fails with ``WinError 206`` (filename/extension too long) when
    the combined env block + command line is oversized — common when the user
    pastes a long brief. Prompt text lives in ``prompt_file``; adapters must
    read that file or pipe stdin instead of stuffing argv/env.
    """
    del prompt, system_prompt  # file path only — see docstring
    env = {
        "DUCKY_PROMPT_TMP_FILE": str(prompt_file),
        "DUCKY_CONV_ID": conv_id,
        "DUCKY_TASK_ID": conv_id,
        "DUCKY_PROJECT_ROOT": project_root or "",
    }
    if identity is not None:
        env.update({k: v for k, v in identity.to_env().items() if v})
        env["DUCKY_CONV_ID"] = conv_id  # the chat id always wins
    if extra:
        env.update(extra)
    return env
