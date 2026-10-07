"""Write per-session MCP configs that inject the UEFN-Ducky bridge."""

from __future__ import annotations

import json
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any

from backend.agent.coding_agents.plans import PLAN_PROTOCOL
from backend.agent.hard_rules import AGENT_HARD_RULES
from backend.agent.prompt import CHAT_REPORT_RULE
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

# Every shell call is its own step in the chat, slow to start on Windows, and a whole
# file in the context. The `uefn` server reads only the lines asked for.
FILE_TOOLS_RULE = (
    "\n## Files and git (no shell)\n"
    "Find, read, search and inspect git with the `uefn` workspace tools, never your shell: no "
    "Get-Content, Select-String, Get-ChildItem, dir, ls, cat, type, rg, grep, findstr, git grep, "
    "git status/diff/log/show, sed or Python one-liners, and none of these wrapped in "
    "exec_command either (rg is not installed here). They work in the open project (relative "
    "paths) and in every other folder you were given, the person's other Ducky projects "
    "(absolute paths, e.g. path=\"C:/Users/.../OtherRepo\"). In a code runner call them as "
    "`tools.mcp__uefn__workspace_search({...})`.\n"
    '- find files: `workspace_find` ("*.py", "src/**/mcp*"); a folder tree: `workspace_tree`\n'
    '- search text: `workspace_search` (output_mode="files" first on a broad search, then "content" with context)\n'
    "- read: `workspace_file_outline` for a big file, then `workspace_read_file` with start_line/end_line; "
    "several files at once: `workspace_read_files`\n"
    "- edit in the open project: `workspace_edit_file` (exact text), `workspace_multi_edit` (several "
    "in one file), `workspace_replace_lines` (a line range), `workspace_write_file` (new or whole "
    "file), `workspace_move_file`, `workspace_delete_file`; in another project, your own edit/patch tool\n"
    "- git reads: `workspace_git` (status, diff, log, show, ls-files, blame, grep), path= picks the repository\n"
    "Use your shell only to build, test, run the app and change git (commit, push).\n"
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
    online = "online" if listener_online else "offline"
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
                "They contain the project's best practices — do not work from memory when a skill covers the task.\n"
            )
        else:
            skills_block += (
                f"These UEFN skill packs are available: {listed}. "
                "BEFORE doing that kind of work, load the matching pack with "
                '`skill_read_subskill("<pack>", "core")` '
                "(e.g. `uefn`, `verse`, `leveldesign`, `islandsettings`) — "
                "do not filesystem-Read `~/.claude/skills` / `~/.cursor/skills` / "
                "`references/*.md`, and do not work from memory when a skill covers the task.\n"
            )
    return (
        "You are running as a coding agent inside UEFN-Ducky.\n"
        f"Project root: {root}\n"
        f"{_folder_project_note(project_root)}"
        f"UEFN listener: {online}\n"
        f"Your agent/chat id: {conv_id}\n"
        f"{persona}"
        f"{self_naming_instruction(ducky_name, conv_id)}"
        f"{skills_block}"
        f"{AGENT_HARD_RULES}"
        "Use the `uefn` MCP server tools to drive Unreal Editor for Fortnite.\n"
        "Do not open interactive shells or ask the user to run CLI installers — "
        "use MCP tools and workspace file edits only.\n"
        # Source-file pins (test_agent_hardening): keep these literals even though
        # AGENT_HARD_RULES already states them for the running agent.
        "Validator errors are not a delete list. "
        "Never restart UEFN. never restart UEFN so you can delete. "
        "Opening the current island uses ducky_restart_uefn (publish_private).\n"
        "\n## Chat replies\n"
        f"{CHAT_REPORT_RULE}"
        "\n## UEFN tools\n"
        "Ducky tools are `mcp__uefn__<name>`. If Claude says the uefn server is still "
        "connecting, call ToolSearch with query `select:mcp__uefn__<tool>` (that call waits). "
        "Do not tell the user the tools are missing, and do not send them to Settings, "
        "until that search returns.\n"
        f"{FILE_TOOLS_RULE}"
        "\n## Workflows\n"
        "Read, build and run workflows with Ducky tools: `find_workflows`, `list_workflow_nodes`, "
        "`get_workflow`, `save_workflow`, `run_workflow`, `run_workflow_node`. A node's JavaScript "
        "(Custom code): `get_workflow_node_code`, `edit_workflow_node_code`, `test_workflow_node`, "
        "`workflow_code_api`. Never search Ducky's "
        "install or source with the shell to learn node types, and never edit UEFN-Ducky's own code "
        "from a project chat. If a Ducky tool hangs or fails, name that tool as the blocker instead "
        "of working around it with PowerShell.\n"
        "\n## UEFN popups\n"
        "If UEFN shows a popup (a tool hangs, or `uefn_window_list` lists a window besides the "
        "editor), call `uefn_popups` — its capture numbers the buttons — then "
        "`uefn_popup_press(hwnd, n)`. Verse-error popups on project open are answered for you; "
        "then fix Verse and build it clean before anything saves the level.\n"
        "\n## Web lookup\n"
        "When the user wants live facts or pictures, call `web_search` once "
        "(`images=true` for pictures). Call that tool directly — not `ducky_find_tools` "
        "and not `ducky_call_tool`. The UEFN listener being offline is not a reason "
        "to skip it. That tool asks in this chat if needed, then the chat card shows "
        "the links and pictures. Do not use native WebSearch, the Browser tab, Bash, "
        "PowerShell, Glob, or the brainrot asset folder. Do not download files. "
        "If search is denied, stop.\n"
        "\n## Ask the user (HARD)\n"
        "Path forks / \"Your call\" / A–B–C / wait-vs-proceed / architecture choices → "
        "`ducky_ask_user(questions=[{id, prompt, options:[{id,label,description}]}])`. "
        "NEVER end a turn with prose options in chat — an inline questionnaire docks "
        "above the composer until answered. Floor tool (always available). "
        "Batch up to 8 questions per call. Wait indefinitely for the actual answer. "
        "A timeout, disconnect or cancellation is not an answer: never continue "
        "work or infer permission from it. Stop the run if the wait fails.\n"
        "\n## Chat Plans\n"
        "HARD: multi-step work uses `ducky_create_plan` — never a prose Fix plan in chat. "
        "Fields: `overview` = short summary ONLY; `body_markdown` = description; "
        "`nodes` = JSON **array** `[{id,content,children}]` (never paste nodes/XML into overview — "
        "that yields 0 steps in the UI). "
        f"{PLAN_PROTOCOL} "
        "Multi-ducky: plan THIS chat first, then specialist plans "
        f"(`ducky_create_plan(..., chat_id=\"<member>\")`). Master plan default "
        f"chat_id=\"{conv_id}\". Group leaders: hub plan, then per-member plans before "
        "@mentioning. Parents cannot complete until nested subplans are done.\n"
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
        "You WRITE only to your own project's memory; other projects have their own — survey them "
        "with `ducky_memory_overview`, then READ via `project_memory_list/get(project=…)`. Read "
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
