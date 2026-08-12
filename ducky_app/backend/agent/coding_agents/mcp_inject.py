"""Write per-session MCP configs that inject the UEFN-Ducky bridge."""

from __future__ import annotations

import json
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any

from backend.agent.hard_rules import AGENT_HARD_RULES
from frontend.mcp_block import build_uefn_server_block
from frontend.settings import PanelSettings, default_app_data_dir

_BRIDGE_HOST_JS = Path(__file__).resolve().parent / "mcp_bridge_host.mjs"


def coding_agents_tmp_dir() -> Path:
    d = default_app_data_dir() / "coding_agents" / "tmp"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _windows_hidden_bridge_block(base: dict[str, Any]) -> dict[str, Any]:
    """Wrap the UEFN stdio bridge so Windows does not flash a console window."""
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


def write_uefn_mcp_config(
    *,
    conv_id: str = "",
    settings: PanelSettings | None = None,
    extra_servers: dict[str, Any] | None = None,
) -> Path:
    """Write a temp mcpServers JSON for Claude/Codex/Cursor launches."""
    s = settings or PanelSettings.load()
    servers = build_uefn_mcp_servers(s)
    if conv_id:
        uefn = servers.get("uefn")
        if isinstance(uefn, dict):
            env = dict(uefn.get("env") or {})
            env["DUCKY_CONV_ID"] = conv_id
            uefn["env"] = env
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
        f"UEFN listener: {online}\n"
        f"Your agent/chat id: {conv_id}\n"
        f"{persona}"
        f"{skills_block}"
        f"{AGENT_HARD_RULES}"
        "Use the `uefn` MCP server tools to drive Unreal Editor for Fortnite "
        "(spawn/wire devices, edit Verse, inspect actors).\n"
        "CRITICAL: NEVER batch or parallelize tool calls — emit exactly ONE tool "
        "call per assistant message, wait for its result, then decide the next "
        "call. This applies to EVERY tool (uefn editor tools, file edits, shell), "
        "not just mutations. Parallel editor calls (spawn_actor, wire_*, set_*, "
        "save_current_level, …) can freeze UEFN, and batching blinds you to "
        "failures you should have reacted to.\n"
        "On spawn_actor always pass label+folder in the same call (same-tick organize).\n"
        "Never Grep/search the UEFN project root, Saved/, Intermediate/, "
        "DerivedDataCache/, or *.uasset — scope to Content/Verse/** only.\n"
        "If a wire fails with 'no compiled hash' / stale reflection: Verse build + "
        "reload_listener, retry ONCE — never hammer. If editor busy: wait, don't re-issue.\n"
        "Verse errors/code FIRST: workspace_list_verse_errors → workspace_read/write "
        "(never ping/get_project_info/ducky_get_errors/listener tools). If a listener "
        "tool does not return immediately it is broken — do not retry.\n"
        "Prefer workspace file tools for Verse under Content/Verse.\n"
        "Do not open interactive shells or ask the user to run CLI installers — "
        "use MCP tools and workspace file edits only.\n"
        "\n## Ask the user (HARD)\n"
        "Path forks / \"Your call\" / A–B–C / wait-vs-proceed / architecture choices → "
        "`ducky_ask_user(questions=[{id, prompt, options:[{id,label,description}]}])`. "
        "NEVER end a turn with prose options in chat — an inline questionnaire docks "
        "above the composer until answered. Floor tool (always available). "
        "Batch up to 8 questions per call.\n"
        "\n## Chat Plans\n"
        "HARD: multi-step work uses `ducky_create_plan` — never a prose Fix plan in chat. "
        "Fields: `overview` = short summary ONLY; `body_markdown` = description; "
        "`nodes` = JSON **array** `[{id,content,children}]` (never paste nodes/XML into overview — "
        "that yields 0 steps in the UI). "
        "Followable `nodes`: Diagnose → Fix → Verify; each leaf = one action + Done-when. "
        "Follow depth-first; tick with `ducky_plan_update_node`. Rewrite the tree before "
        "changing approach — do not thrash off-plan. "
        "Multi-ducky: plan THIS chat first, then specialist plans "
        f"(`ducky_create_plan(..., chat_id=\"<member>\")`). Master plan default "
        f"chat_id=\"{conv_id}\". Group leaders: hub plan, then per-member plans before "
        "@mentioning. Parents cannot complete until nested subplans are done. "
        "Templates: `ducky_create_plan_template` / `ducky_instantiate_plan_template`.\n"
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
    if extra:
        env.update(extra)
    return env
