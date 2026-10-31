"""Approval cards for coding agents (Claude Code's ``--permission-prompt-tool``).

Claude Code runs headless (``-p``). Without a prompt tool, every call its permission
mode does not already allow (a shell command, a write outside the project, a web fetch)
is denied on the spot, and the agent just reports an error. The Anthropic plugin points
``--permission-prompt-tool`` at ``ducky_permission_prompt``, which turns each of those
into an Allow/Deny card in the agent's chat (the ``ducky_ask_user`` path) and answers
Claude Code with ``{"behavior": "allow", "updatedInput": ...}`` or
``{"behavior": "deny", "message": ...}``.

Reads (Read/Glob/Grep/LS) never ask. "Always allow <command> in this chat" is remembered for
the chat (its own ``workspace_state`` row, so saving the chat can't drop it) for plain commands only; a command chained with
``;``/``&&``/``|`` or spanning lines is never remembered on its own, and pushes, force/reset/
clean, deletes, PR/release actions and build/publish/deploy scripts are never remembered that
way. "Allow everything in this chat" (rule ``*``) stops every card in that chat and in the
runs it starts (a sub-agent, a group member, a workflow ducky it sent work to, the members
Ducky starts on its team plan: each run saves who started it), those too. A card from a chat
another chat started also shows in the chat at the top of those links (``starter_root``),
where the person works with the team. The chat's context panel shows it and turns it off;
deleting a chat drops its rows.

Each chat also has an approval mode, picked from the permissions button in its composer:
"Ask before changes" (file edits ask too), "Accept edits" (the default: edits go ahead,
commands ask) and "Allow everything" (the ``*`` rule above). Claude Code reads it through
``claude_permission_mode`` when a turn starts; the embedded Ducky agent through
``ducky_tool_gate`` before each file change or destructive tool.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shlex
import time
from typing import Any

from backend.server import mcp

_ALLOW_ONCE = "once"
_ALLOW_ALWAYS = "always"
_ALLOW_ALL = "all"
_DENY = "deny"
_QUESTION_ID = "agent_permission"
_ALL_RULE = "*"

_SHELL_TOOLS = frozenset({"Bash", "PowerShell"})
_FILE_TOOLS = frozenset({"Write", "Edit", "MultiEdit", "NotebookEdit"})
# Change nothing, so they never ask.
_READ_ONLY_TOOLS = frozenset({"Read", "Glob", "Grep", "LS", "NotebookRead"})

# Git takes options before its subcommand: "git -C <folder> push" and "git -c k=v reset --hard"
# must read as the push and the reset they are.
_GIT_PREFIX = r"\bgit(?:\.exe)?(?:\s+(?:-[Cc]\s+(?:\"[^\"]*\"|'[^']*'|\S+)|--[\w-]+(?:=\S+)?))*\s+"

# Never remembered: each one asks, with the reason on the card.
_RISKY: tuple[tuple[re.Pattern[str], str], ...] = tuple(
    (re.compile(pattern.replace(r"\bgit\s+", _GIT_PREFIX), re.IGNORECASE), reason)
    for pattern, reason in (
        (r"\bgit\s+push\b", "Pushes commits to a remote."),
        (r"\bgit\s+reset\s+--hard\b", "Discards local changes."),
        (r"\bgit\s+clean\b", "Deletes untracked files."),
        (r"\bgit\s+branch\s+-D\b", "Deletes a branch."),
        (r"\bgit\s+(rebase|filter-branch|filter-repo)\b", "Rewrites history."),
        (r"\bgit\s+commit\b.*--amend\b", "Rewrites the last commit."),
        (r"\bgit\s+(checkout|restore)\s+(--\s|\.)", "Discards local changes."),
        (r"\bgit\s+stash\s+(drop|clear)\b", "Deletes stashed changes."),
        (r"\bgit\b.*\s(-f|--force|--force-with-lease)\b", "Forces a git operation."),
        (r"(^|\s)--force\b", "Forces the operation."),
        (r"\bgh\s+(pr\s+(create|merge|close|review)|release|repo\s+(create|delete|edit)|issue\s+(create|close|comment)|api)\b",
         "Changes something on GitHub."),
        (r"\brm\s+-[a-z]*[rf]", "Deletes files."),
        (r"\b(Remove-Item|rmdir|rd)\b", "Deletes files."),
        (r"\bdel\s", "Deletes files."),
        (r"\b(npm|pnpm|yarn)\s+publish\b|\btwine\s+upload\b", "Publishes a package."),
        (r"publish|release\.py|build_exes\.py|make_release_installer|\bdeploy\b", "Builds, publishes or deploys."),
        (r"\b(Stop-Process|taskkill|shutdown)\b", "Stops processes or the machine."),
        (r"\b(curl|wget|Invoke-WebRequest|iwr)\b.*\|\s*(sh|bash|iex|Invoke-Expression)\b", "Runs a downloaded script."),
    )
)

# Changes something remote: a push in any form, a GitHub repo / release / PR, a publish.
_REMOTE_CHANGE_RE = re.compile(
    r"\bgit(?:\.exe)?\b[^;&|\n]*\spush\b|--push\b|\bgh\s+(?:repo\s+(?:create|edit|sync)|release|pr\s+(?:create|merge))\b"
    r"|\bgh\s+(?:api|workflow\s+run)\b|\b(?:npm|pnpm|yarn)\s+publish\b|\btwine\s+upload\b|--publish\b|\bpublish\w*\.py\b",
    re.IGNORECASE,
)
_CHAIN_RE = re.compile(r"(;|&&|\|\||\||`|\$\(|>|<|\n)")


def _conv_id() -> str:
    from backend.tools.panel.panel_ui import _resolve_ask_user_conv_id

    return _resolve_ask_user_conv_id()


def _project_root() -> str:
    raw = (os.environ.get("DUCKY_PROJECT_ROOT") or "").strip()
    if raw:
        return raw
    try:
        from frontend.settings import PanelSettings

        return PanelSettings.load().uefn_project_root.strip()
    except Exception:
        return ""


def _command_of(tool_input: dict[str, Any]) -> str:
    return str(tool_input.get("command") or "").strip()


def _command_key(command: str) -> str:
    """Rule key for a plain command: program + subcommand (``git status``, ``npm run test``)."""
    try:
        words = shlex.split(command, posix=False)
    except ValueError:
        words = command.split()
    plain: list[str] = []
    for word in words:
        if word.startswith(("'", '"')):
            break  # a quoted script or message body is not part of the rule
        if word and not word.startswith("-"):
            plain.append(word)
    words = plain
    if not words:
        return ""
    head = os.path.basename(words[0]).lower().removesuffix(".exe")
    take = 3 if head in {"npm", "pnpm", "yarn", "uv", "py", "python", "python3"} and len(words) > 2 else 2
    return " ".join([head, *words[1:take]]).strip()


def _risk(command: str) -> str:
    for pattern, reason in _RISKY:
        if pattern.search(command):
            return reason
    return ""


def describe(tool_name: str, tool_input: dict[str, Any], *, project_root: str = "") -> dict[str, Any]:
    """What the card shows, and the rule key "always allow" would remember ('' = never)."""
    name = (tool_name or "").strip() or "tool"
    if name in _SHELL_TOOLS:
        command = _command_of(tool_input)
        reason = _risk(command)
        chained = bool(_CHAIN_RE.search(command))
        key = "" if reason or chained or not command else f"{name}:{_command_key(command)}"
        desc = str(tool_input.get("description") or "").strip()
        return {
            "prompt": f"Allow the agent to run this command?{f' ({desc})' if desc else ''}",
            "detail": command or "(empty command)",
            "warning": reason,
            "risky": bool(reason) or not command,
            "rule": key,
            "rule_label": _command_key(command) if key else "",
        }
    if name in _FILE_TOOLS:
        path = str(tool_input.get("file_path") or tool_input.get("notebook_path") or "").strip()
        folder = os.path.dirname(path) if path else ""
        return {
            "prompt": f"Allow the agent to {'create' if name == 'Write' else 'edit'} this file?",
            "detail": path or json.dumps(tool_input, ensure_ascii=False)[:2000],
            "warning": "Outside the project folder." if path and project_root and not _is_under(path, project_root) else "",
            "risky": False,
            "rule": f"{name}:{folder.lower()}" if folder else "",
            "rule_label": f"edits in {folder}" if folder else "",
        }
    detail = str(tool_input.get("url") or tool_input.get("query") or "") or json.dumps(tool_input, ensure_ascii=False)[:2000]
    return {
        "prompt": f"Allow the agent to use {name}?",
        "detail": detail,
        "warning": "",
        "risky": False,
        "rule": name,
        "rule_label": name,
    }


def _is_under(path: str, root: str) -> bool:
    try:
        full = os.path.realpath(os.path.abspath(path))
        base = os.path.realpath(os.path.abspath(root))
        return os.path.commonpath([full, base]) == base
    except ValueError:
        return False


def _load_conv(conv_id: str):
    if not conv_id:
        return None
    try:
        from frontend.chat_store import load_conversation

        return load_conversation(conv_id)
    except Exception:
        return None


# Remembered approvals live in their own row per chat, not on the chat record: the app
# saves its in-memory chat during a turn, which would wipe a rule written here mid-turn.
_RULES_TABLE = "workspace_state"


def _rules_key(conv_id: str) -> str:
    return f"agent_allow:{conv_id}"


def _rules_in_db() -> bool:
    try:
        from backend.store.switch import use_db

        return use_db(_RULES_TABLE)
    except Exception:
        return False


def _rules(conv_id: str) -> list[str]:
    """What this chat said to always allow ("*" = everything that isn't risky)."""
    if not conv_id:
        return []
    if _rules_in_db():
        from backend.store.repos import kv

        # A read that failed counted as "no rules": a team whose chats allowed everything
        # had its commands wait on approval cards nobody saw (Oct 12 2026). Retry, and say so.
        for attempt in range(3):
            try:
                doc = kv.get_doc(_RULES_TABLE, _rules_key(conv_id))
                return [str(r) for r in doc if str(r).strip()] if isinstance(doc, list) else []
            except Exception:
                if attempt == 2:
                    logging.getLogger(__name__).warning(
                        "could not read the approval rules of chat %s", conv_id, exc_info=True)
                    return []
                time.sleep(0.2)
    conv = _load_conv(conv_id)  # files store: on the chat itself
    return list(getattr(conv, "agent_allow_rules", None) or []) if conv is not None else []


def _started_key(conv_id: str) -> str:
    return f"agent_started_by:{conv_id}"


def note_started_by(conv_id: str, starter: str) -> None:
    """Save who started this chat's run (or clear it: started from its own chat, a schedule,
    the Workflows screen). "Allow everything" passes down this link only, never through a
    group hub or to a run no chat started."""
    conv_id, starter = (conv_id or "").strip(), (starter or "").strip()
    if not conv_id or not _rules_in_db():
        return
    try:
        from backend.store.repos import kv

        if starter and starter != conv_id:
            kv.set_doc(_RULES_TABLE, _started_key(conv_id), starter)
        else:
            kv.delete_doc(_RULES_TABLE, _started_key(conv_id))
    except Exception:
        pass


def _started_by(conv_id: str) -> str:
    if not conv_id or not _rules_in_db():
        return ""
    try:
        from backend.store.repos import kv

        doc = kv.get_doc(_RULES_TABLE, _started_key(conv_id))
    except Exception:
        return ""
    return doc.strip() if isinstance(doc, str) else ""


def _chat_exists(conv_id: str) -> bool:
    """A deleted chat passes nothing down (store errors count as gone: the card shows)."""
    try:
        from backend.store.switch import use_db

        if use_db("chats"):
            from backend.store.repos import chats

            return chats.conv_get(conv_id, with_messages=False) is not None
    except Exception:
        return False
    return _load_conv(conv_id) is not None


def allow_source(conv_id: str) -> str:
    """The chat whose "Allow everything" covers this one: itself, or the chat that started
    its run (and so on up); '' when none."""
    seen: list[str] = []
    cid = (conv_id or "").strip()
    while cid and cid not in seen and len(seen) < 8:
        if seen and not _chat_exists(cid):
            return ""
        if _ALL_RULE in _rules(cid):
            return cid
        seen.append(cid)
        cid = _started_by(cid)
    return ""


_EVERYWHERE_TITLE = "Settings → Permissions"


def allows_everywhere() -> bool:
    """The person's own "Allow everything in every chat" switch (no AI can set it)."""
    try:
        from frontend.settings import PanelSettings

        return bool(PanelSettings.load().allow_everything_all_chats)
    except Exception:
        return False


def allow_state(conv_id: str) -> dict[str, Any]:
    """For the chat's context panel: on, set here (can be turned off here) or from which chat
    (or from Settings, for every chat)."""
    src = allow_source(conv_id)
    out: dict[str, Any] = {"on": bool(src), "own": bool(src) and src == (conv_id or "").strip(), "from_title": ""}
    if not out["own"] and allows_everywhere():
        return {"on": True, "own": False, "from_title": _EVERYWHERE_TITLE}
    if src and not out["own"]:
        title = ""
        try:
            from backend.store.repos import chats

            doc = chats.conv_get(src, with_messages=False)
            title = str((doc or {}).get("title") or "")
        except Exception:
            conv = _load_conv(src)
            title = str(getattr(conv, "title", "") or "") if conv is not None else ""
        out["from_title"] = title or "the chat that started it"
    return out


def allows_everything(conv_id: str) -> bool:
    """"Allow everything" was picked in this chat, in the chat that started its run, or for
    every chat in Settings. Oct 31 2026: chats an outside agent started (a harness race) had
    no chat to take it from, and their sandbox asked though the person allows everything."""
    return allows_everywhere() or bool(allow_source(conv_id))


def starter_root(conv_id: str) -> str:
    """The chat at the top of this chat's "started by" links (a team's lead, the chat that
    spawned a helper), or '' when no other chat started it. A card for this chat shows there
    too: on Oct 15 2026 a team member's test command waited 120 s on a card in its own chat,
    which nobody had open, and the whole team stopped."""
    seen: list[str] = []
    top = ""
    cid = (conv_id or "").strip()
    while cid and cid not in seen and len(seen) < 8:
        seen.append(cid)
        up = _started_by(cid)
        if not up or up in seen or not _chat_exists(up):
            break
        top = cid = up
    return top


# The approval mode of a chat. "edits" is the default and keeps no row; "all" is the "*"
# rule (the same switch as an approval card's "Allow everything in this chat"); "ask"
# keeps its own row next to the chat's rules.
MODE_ASK = "ask"
MODE_EDITS = "edits"
MODE_ALL = "all"
MODES = (MODE_ASK, MODE_EDITS, MODE_ALL)
MODE_TEXT: dict[str, tuple[str, str]] = {
    MODE_ASK: ("Ask before changes", "Asks before editing files or running commands."),
    MODE_EDITS: ("Accept edits", "Edits files without asking. Asks before commands."),
    MODE_ALL: ("Allow everything", "Never asks in this chat or the agents it starts."),
}


def _mode_key(conv_id: str) -> str:
    return f"agent_mode:{conv_id}"


def _own_mode(conv_id: str) -> str:
    """The mode row this chat keeps ("ask"), or '' for the default."""
    if not conv_id or not _rules_in_db():
        return ""
    try:
        from backend.store.repos import kv

        doc = kv.get_doc(_RULES_TABLE, _mode_key(conv_id))
    except Exception:
        return ""
    return MODE_ASK if doc == MODE_ASK else ""


def _store_own_mode(conv_id: str, mode: str) -> None:
    if not conv_id or not _rules_in_db():
        return
    try:
        from backend.store.repos import kv

        if mode == MODE_ASK:
            kv.set_doc(_RULES_TABLE, _mode_key(conv_id), MODE_ASK)
        else:
            kv.delete_doc(_RULES_TABLE, _mode_key(conv_id))
    except Exception:
        pass


def permission_mode(conv_id: str) -> str:
    """This chat's approval mode: "all" while Allow everything covers it (picked here or in
    the chat that started its run), else "ask" or the default "edits"."""
    conv_id = (conv_id or "").strip()
    if allows_everything(conv_id):
        return MODE_ALL
    return MODE_ASK if _own_mode(conv_id) == MODE_ASK else MODE_EDITS


def set_permission_mode(conv_id: str, mode: str) -> None:
    """Pick this chat's mode. Its remembered rules stay; Allow everything goes on or off with it."""
    if mode not in MODES:
        raise ValueError(f"Unknown permission mode: {mode!r}")
    conv_id = (conv_id or "").strip()
    if not conv_id:
        return
    set_allow_everything(conv_id, mode == MODE_ALL)
    _store_own_mode(conv_id, mode)


def claude_permission_mode(conv_id: str, configured: str = "") -> str:
    """Claude Code's ``--permission-mode`` for this chat's next turn: "default" sends file
    edits to the approval card too; otherwise the mode set in Settings (acceptEdits)."""
    if permission_mode(conv_id) == MODE_ASK:
        return "default"
    return (configured or "").strip() or "acceptEdits"


# Ducky's own tools that change project files: "Ask before changes" shows a card first.
_DUCKY_MOVE_TOOLS = frozenset({"workspace_delete_file", "workspace_move_file"})
_DUCKY_EDIT_RULE = "ducky:edits"


def _ducky_file_tool(tool_name: str) -> bool:
    from backend.agent.write_claim_guard import WRITE_TOOLS

    return tool_name in WRITE_TOOLS or tool_name in _DUCKY_MOVE_TOOLS


def ducky_tool_gate(conv_id: str, tool_name: str, *, destructive: bool) -> str:
    """What the embedded Ducky agent does before this tool: "run", "ask" (approval card) or
    "refuse". Under "Ask before changes" it asks before file changes and destructive tools.
    Otherwise as it always has: file changes run, destructive tools are refused unless the
    chat allows everything (an unattended workflow run never waits on a card)."""
    if not destructive and not _ducky_file_tool(tool_name):
        return "run"
    mode = permission_mode(conv_id)
    if mode == MODE_ALL:
        return "run"
    if mode == MODE_ASK:
        return "ask"
    return "refuse" if destructive else "run"


def _ducky_card(tool_name: str, arguments: dict[str, Any], *, agent_label: str = "Ducky") -> dict[str, Any]:
    args = arguments if isinstance(arguments, dict) else {}
    raw = json.dumps(args, ensure_ascii=False, default=str)[:2000]
    if _ducky_file_tool(tool_name):
        path = str(args.get("relative_path") or args.get("path") or args.get("source") or "").strip()
        if tool_name == "workspace_move_file":
            verb, detail = "move", f"{path} → {str(args.get('destination') or '').strip()}"
        else:
            verb, detail = ("delete" if tool_name == "workspace_delete_file" else "edit"), path
        rule = _DUCKY_EDIT_RULE if verb == "edit" else ""
        return {
            "prompt": f"Allow {agent_label} to {verb} this file?",
            "detail": detail or raw,
            "warning": "",
            "risky": False,
            "rule": rule,
            "rule_label": "file edits" if rule else "",
        }
    return {
        "prompt": f"Allow {agent_label} to run {tool_name}?",
        "detail": raw,
        "warning": "This change is hard to undo.",
        "risky": True,
        "rule": "",
        "rule_label": "",
    }


def approve_ducky_tool(
    conv_id: str, tool_name: str, arguments: dict[str, Any], *, agent_label: str = "Ducky"
) -> bool:
    """Approval card for one embedded Ducky tool call (skipped when a rule already allows it)."""
    card = _ducky_card(tool_name, arguments, agent_label=agent_label)
    rule = str(card.get("rule") or "")
    if rule and rule in _rules(conv_id):
        return True
    answer, _note = _ask(card, tool_name or "tool")
    if answer == _ALLOW_ALL:
        _remember(conv_id, _ALL_RULE)
        return True
    if answer == _ALLOW_ALWAYS and rule:
        _remember(conv_id, rule)
        return True
    return answer == _ALLOW_ONCE


_FOLDER_RULE = "folder:"
# Folders the person always allowed, for every ducky: one ducky's yes never makes the next
# one ask again (Oct 23 2026, the owner: duckies work on any project, no barriers).
_SHARED_FOLDERS_KEY = "agent_allow_folders"


def _shared_folders() -> list[str]:
    if not _rules_in_db():
        return []
    try:
        from backend.store.repos import kv

        doc = kv.get_doc(_RULES_TABLE, _SHARED_FOLDERS_KEY)
    except Exception:
        return []
    return [str(f) for f in doc if str(f).strip()] if isinstance(doc, list) else []


def _store_shared_folders(folders: list[str]) -> None:
    if not _rules_in_db():
        return
    from backend.store.repos import kv

    kv.set_doc(_RULES_TABLE, _SHARED_FOLDERS_KEY, folders[-200:])


def _share_folder(folder: str) -> bool:
    """Allow ``folder`` for every ducky. False when there is no shared store (files store)."""
    if not _rules_in_db():
        return False
    folders = _shared_folders()
    if folder not in folders:
        _store_shared_folders([*folders, folder])
    return True


def approved_folders(conv_id: str) -> list[str]:
    """Folders outside the person's projects that were always allowed: for every ducky,
    and the ones this chat allowed before they were shared."""
    own = [r[len(_FOLDER_RULE):] for r in _rules(conv_id) if r.startswith(_FOLDER_RULE)]
    return list(dict.fromkeys([*_shared_folders(), *own]))


def approve_outside_folder(conv_id: str, folder: str, path: str) -> str:
    """Card for an agent reaching ``path`` in ``folder``, outside the person's Ducky projects.
    "" when it may go ahead (asked now, or allowed before), else why not, for the agent.

    Oct 21 2026: the file tools refused every such path outright, so agents told people
    they had "no permission" for files the person had just asked them to change."""
    if allows_everything(conv_id) or folder in approved_folders(conv_id):
        return ""
    from backend.agent.coding_agents.base import coding_agent_label

    card = {
        "prompt": f"Allow {coding_agent_label(_chat_agent(conv_id))} to use files in this folder? "
                  "It is outside your Ducky projects.",
        "detail": folder if os.path.normcase(path) == os.path.normcase(folder) else f"{folder}\n(for {path})",
        "warning": "",
        "risky": False,
        "rule": _FOLDER_RULE + folder,
        "rule_label": "this folder",
        "always_label": "Always allow this folder",
        "always_description": "Every ducky may use it. Don't ask again.",
    }
    answer, note = _ask(card, "Folder outside your projects")
    if answer == _ALLOW_ALL:
        _remember(conv_id, _ALL_RULE)
    elif answer == _ALLOW_ALWAYS and not _share_folder(folder):
        _remember(conv_id, card["rule"])
    if answer in (_ALLOW_ALL, _ALLOW_ALWAYS, _ALLOW_ONCE):
        return ""
    if answer == _DENY:
        said = f" and said: {note}" if note else ""
        return f"The person said no to {folder}{said}. Don't retry it; ask them what to do instead."
    return f"Couldn't ask the person about {folder} ({answer}). Tell them it is outside their Ducky projects."


_DELETE_OPS = frozenset({"delete"})
_MOVE_OPS = frozenset({"rename", "move"})


def approve_agent_change(conv_id: str, agent: str, op: str, paths: tuple[str, ...]) -> bool:
    """"Ask before changes" for a coding agent (Codex, Claude Code, Cursor) changing a file
    through Ducky's tools: the same card Ducky's own agent gets, naming that agent."""
    from backend.agent.coding_agents.base import coding_agent_label

    if op in _DELETE_OPS:
        tool, args = "workspace_delete_file", {"path": paths[-1]}
    elif op in _MOVE_OPS and len(paths) > 1:
        tool, args = "workspace_move_file", {"path": paths[0], "destination": paths[-1]}
    else:
        tool, args = "workspace_write_file", {"path": paths[-1]}
    return approve_ducky_tool(conv_id, tool, args, agent_label=coding_agent_label(agent))


SANDBOX_RULE = "sandbox:commands"


def allows_sandbox(conv_id: str) -> bool:
    """This chat runs sandbox commands without a card: "Allow everything", or its own
    "Allow sandbox commands in this chat"."""
    conv_id = (conv_id or "").strip()
    return bool(conv_id) and (allows_everything(conv_id) or SANDBOX_RULE in _rules(conv_id))


def allow_sandbox(conv_id: str) -> None:
    if (conv_id or "").strip():
        _remember(conv_id.strip(), SANDBOX_RULE)


def rule_label(rule: str) -> str:
    """How a remembered rule reads in the permissions pop-up ("Bash: git status")."""
    if rule == _DUCKY_EDIT_RULE:
        return "Ducky: file edits"
    if rule == SANDBOX_RULE:
        return "Sandbox: commands"
    tool, sep, rest = rule.partition(":")
    return f"{tool}: {rest}" if sep and rest else rule


def allowed_rules(conv_id: str) -> list[dict[str, str]]:
    """What this chat may do without asking, apart from Allow everything (that is the
    mode): its own rules and the folders allowed for every ducky."""
    rules = [r for r in _rules(conv_id) if r != _ALL_RULE]
    rules += [_FOLDER_RULE + f for f in _shared_folders() if _FOLDER_RULE + f not in rules]
    return [{"rule": r, "label": rule_label(r)} for r in rules]


def forget_rule(conv_id: str, rule: str) -> None:
    rules = _rules(conv_id)
    if rule in rules:
        _store_rules(conv_id, [r for r in rules if r != rule])
    if rule.startswith(_FOLDER_RULE):
        shared = _shared_folders()
        folder = rule[len(_FOLDER_RULE):]
        if folder in shared:
            _store_shared_folders([f for f in shared if f != folder])


def clear_rules(conv_id: str) -> None:
    """Drop every remembered rule; the chat's mode (Allow everything too) stays."""
    rules = _rules(conv_id)
    kept = [r for r in rules if r == _ALL_RULE]
    if kept != rules:
        _store_rules(conv_id, kept)


def _chat_agent(conv_id: str) -> str:
    try:
        from backend.store.switch import use_db

        if use_db("chats"):
            from backend.store.repos import chats

            doc = chats.conv_get(conv_id, with_messages=False) or {}
            return str(doc.get("coding_agent") or "ducky")
    except Exception:
        pass
    conv = _load_conv(conv_id)
    return str(getattr(conv, "coding_agent", "") or "ducky") if conv is not None else "ducky"


def _agent_registration(agent_id: str) -> dict[str, Any]:
    try:
        from backend.uefn_plugins.host import get_coding_agent_registration

        reg = get_coding_agent_registration(agent_id)
        if reg is None:
            from backend.agent.coding_agents.base import normalize_coding_agent

            alias = normalize_coding_agent(agent_id)
            reg = get_coding_agent_registration(alias) if alias != "ducky" else None
        return reg or {}
    except Exception:
        return {}


def _agent_modes(agent_id: str) -> tuple[str, ...]:
    """The modes this agent honours; empty when it never asks inside Ducky."""
    if agent_id == "ducky":
        return MODES
    reg = _agent_registration(agent_id)
    declared = reg.get("chat_permission_modes")
    if declared:
        return tuple(m for m in MODES if m in declared)
    if "permission_mode" in (reg.get("settings_defaults") or {}):
        # An older Claude Code plugin: Ducky's approval card and Allow everything work,
        # but it always accepts edits.
        return (MODE_EDITS, MODE_ALL)
    return ()


# A plugin the Store installed, or one installed before Ducky recorded where plugins came from.
_STORE_UPDATABLE = ("store", "")


def _agent_plugin(agent_id: str) -> dict[str, str]:
    """The installed plugin that adds this agent: id, name, version and where it came from."""
    pid = str(_agent_registration(agent_id).get("plugin_id") or "").strip()
    if not pid:
        return {}
    try:
        from backend.uefn_plugins.plugin_version import format_plugin_version
        from backend.uefn_plugins.store import load_plugin_manifest

        manifest = load_plugin_manifest(pid) or {}
        version = format_plugin_version(manifest["version"]) if manifest.get("version") else ""
    except Exception:
        manifest, version = {}, ""
    return {
        "id": pid,
        "label": str(manifest.get("label") or pid),
        "version": str(version or ""),
        # "" = installed before Ducky kept where a plugin came from: the Store can update it.
        "source": str(manifest.get("source") or ""),
    }


def _agents_that_ask() -> list[str]:
    """Agents on this PC that stop for approval cards: Ducky, then any plugin agent that can."""
    from backend.agent.coding_agents.base import coding_agent_label

    labels = [coding_agent_label("ducky")]
    try:
        from backend.uefn_plugins.host import registered_coding_agent_ids

        for aid in registered_coding_agent_ids():
            if aid != "ducky" and MODE_ASK in _agent_modes(aid):
                label = coding_agent_label(aid)
                if label not in labels:
                    labels.append(label)
    except Exception:
        pass
    return labels


def chat_permissions(conv_id: str, agent: str = "") -> dict[str, Any]:
    """Everything the chat's permissions pop-up shows. ``agent``: the agent its next turn
    uses (the composer's pick), else the chat's own."""
    from backend.agent.coding_agents.base import coding_agent_label

    conv_id = (conv_id or "").strip()
    agent_id = (agent or "").strip().lower().replace("-", "_") or _chat_agent(conv_id)
    label = coding_agent_label(agent_id)
    honoured = _agent_modes(agent_id)
    allow = allow_state(conv_id)
    inherited = bool(allow["on"]) and not allow["own"]
    mode = MODE_ALL if allow["on"] else (MODE_ASK if _own_mode(conv_id) == MODE_ASK else MODE_EDITS)
    # An agent that never stops for a card: say what it does instead of offering modes it ignores.
    note = hint = ""
    if not honoured:
        note = f"{label} edits files and runs commands without asking. Ducky can't stop it to ask first."
        others = [name for name in _agents_that_ask() if name != label]
        if others:
            hint = f"To approve changes first, switch this chat to {' or '.join(others)}."
    # An agent whose plugin is older than the modes it is missing: name that plugin and its version.
    update: dict[str, Any] | None = None
    if honoured and any(m not in honoured for m in MODES):
        plugin = _agent_plugin(agent_id)
        if plugin:
            update = {
                "plugin_id": plugin["id"],
                "label": plugin["label"],
                "version": plugin["version"],
                # Only a Store install can be updated from the Store; a local copy is the owner's own.
                "from_store": plugin["source"] in _STORE_UPDATABLE,
            }
    modes: list[dict[str, Any]] = []
    for mode_id in MODES:
        name, description = MODE_TEXT[mode_id]
        if not honoured:
            reason = note
        elif mode_id not in honoured:
            if update:
                have = f" (this PC has {update['version']})" if update["version"] else ""
                reason = f"Needs a newer {update['label']} plugin{have}."
            else:
                reason = f"Needs a newer version of the plugin that adds {label}."
        elif inherited and mode_id != MODE_ALL:
            reason = f"Allow everything is on from {allow['from_title']}. Turn it off there."
        else:
            reason = ""
        modes.append({"id": mode_id, "label": name, "description": description,
                      "available": not reason, "reason": reason})
    return {
        "mode": mode,
        "label": MODE_TEXT[mode][0],
        "own": not inherited,
        "from_title": str(allow["from_title"]) if inherited else "",
        "agent": agent_id,
        "agent_label": label,
        "asks": bool(honoured),
        "note": note,
        "hint": hint,
        "update": update,
        "modes": modes,
        "rules": allowed_rules(conv_id),
    }


def update_agent_plugin(agent: str) -> dict[str, Any]:
    """Install the Store's newest version of the plugin that adds ``agent`` (the pop-up's Update)."""
    agent_id = (agent or "").strip().lower().replace("-", "_")
    plugin = _agent_plugin(agent_id)
    if not plugin:
        raise ValueError("Ducky can't tell which plugin adds this agent.")
    if plugin["source"] not in _STORE_UPDATABLE:
        raise ValueError(
            f"Your {plugin['label']} plugin is a local copy, so the Store can't update it. "
            "Update that copy, or remove it and install it from the Store."
        )
    from frontend.duckyos_account import DuckyOSAccountError, store_download_and_install

    try:
        result = store_download_and_install(plugin["id"], replace=True, is_update=True)
    except DuckyOSAccountError as exc:
        raise ValueError(exc.message or f"Could not update {plugin['label']}.") from exc
    if not result.get("ok"):
        raise ValueError(str(result.get("error") or f"Could not update {plugin['label']}."))
    try:
        from frontend.ui_web.agent_modes import push_ui_event

        push_ui_event({"type": "uefn_plugins_changed"})
    except Exception:
        pass
    return {"ok": True, "plugin_id": plugin["id"], "version": str(result.get("version") or "")}


def _store_rules(conv_id: str, rules: list[str]) -> None:
    if _rules_in_db():
        try:
            from backend.store.repos import kv

            if rules:
                kv.set_doc(_RULES_TABLE, _rules_key(conv_id), rules)
            else:
                kv.delete_doc(_RULES_TABLE, _rules_key(conv_id))
        except Exception:
            pass
        return
    conv = _load_conv(conv_id)
    if conv is None:
        return
    conv.agent_allow_rules = rules
    try:
        from frontend.chat_store import save_conversation

        save_conversation(conv)
    except Exception:
        pass


def set_allow_everything(conv_id: str, on: bool) -> None:
    """Turn this chat's own "Allow everything" on or off (its other remembered rules stay)."""
    conv_id = (conv_id or "").strip()
    if not conv_id:
        return
    if on:
        _remember(conv_id, _ALL_RULE)
    elif _ALL_RULE in _rules(conv_id):
        _store_rules(conv_id, [r for r in _rules(conv_id) if r != _ALL_RULE])


def forget_chat(conv_id: str) -> None:
    """A deleted chat: drop its remembered approvals and who-started-it row."""
    if not conv_id or not _rules_in_db():
        return
    try:
        from backend.store.repos import kv

        kv.delete_doc(_RULES_TABLE, _rules_key(conv_id))
        kv.delete_doc(_RULES_TABLE, _started_key(conv_id))
        kv.delete_doc(_RULES_TABLE, _mode_key(conv_id))
    except Exception:
        pass


def approvals_of(conv_id: str) -> dict[str, Any]:
    """What to carry to a recycled chat's twin (read it before the old chat is deleted)."""
    return {"rules": _rules(conv_id), "started_by": _started_by(conv_id), "mode": _own_mode(conv_id)}


def restore_approvals(conv_id: str, saved: dict[str, Any]) -> None:
    for rule in saved.get("rules") or []:
        _remember(conv_id, str(rule))
    note_started_by(conv_id, str(saved.get("started_by") or ""))
    if saved.get("mode") == MODE_ASK:
        _store_own_mode(conv_id, MODE_ASK)


def _remember(conv_id: str, rule: str) -> None:
    if not conv_id or not rule:
        return
    rules = _rules(conv_id)
    if rule in rules:
        return
    _store_rules(conv_id, (rules + [rule])[-200:])


def _ask(card: dict[str, Any], tool_name: str) -> tuple[str, str]:
    """Show the card: (once / always / all / deny, or an error string; the user's typed note)."""
    from backend.tools.panel.panel_ui import ducky_ask_user

    options = [{"id": _ALLOW_ONCE, "label": "Allow once", "description": "Run it this time. Ask again next time."}]
    if card.get("rule"):
        options.append(
            {
                "id": _ALLOW_ALWAYS,
                "label": card.get("always_label") or f"Always allow {card['rule_label']} in this chat",
                "description": card.get("always_description") or "Don't ask again for this in this chat.",
            }
        )
    options.append(
        {
            "id": _ALLOW_ALL,
            "label": "Allow everything in this chat",
            "description": "Never ask again in this chat or the agents it starts: pushes, deletes and publishes run too. "
            "Turn it off with the chat's permissions button.",
        }
    )
    options.append({"id": _DENY, "label": "Deny", "description": "Don't run it. The agent is told you said no."})
    question: dict[str, Any] = {
        "id": _QUESTION_ID,
        "prompt": card["prompt"],
        "detail": card.get("detail") or "",
        "options": options,
        "allow_free_text": True,
    }
    if card.get("warning"):
        question["warning"] = card["warning"]
    raw = ducky_ask_user([question], title=f"Approval needed: {tool_name}")
    try:
        out = json.loads(raw) if isinstance(raw, str) else raw
    except Exception:
        return "Could not show the approval card.", ""
    if not isinstance(out, dict):
        return "Could not show the approval card.", ""
    if out.get("error"):
        return str(out["error"]), ""
    row = (out.get("answers") or {}).get(_QUESTION_ID) or {}
    selected = {str(item) for item in (row.get("selected") or [])}
    note = str(row.get("text") or "").strip()
    for choice in (_DENY, _ALLOW_ALL, _ALLOW_ALWAYS, _ALLOW_ONCE):
        if choice in selected:
            return choice, note
    # Skipped, or typed a reply instead of picking: that is a "no" (with the reason, if any).
    return _DENY, note


def decide(tool_name: str, tool_input: dict[str, Any], *, conv_id: str = "", project_root: str = "") -> dict[str, Any]:
    """Claude Code permission result for one tool call (asks the user unless remembered)."""
    tool_input = dict(tool_input or {})
    if (tool_name or "").strip() in _READ_ONLY_TOOLS:
        return {"behavior": "allow", "updatedInput": tool_input}
    card = describe(tool_name, tool_input, project_root=project_root)
    rule = str(card.get("rule") or "")
    if allows_everything(conv_id):
        return {"behavior": "allow", "updatedInput": tool_input}
    if not card.get("risky") and rule and rule in _rules(conv_id):
        return {"behavior": "allow", "updatedInput": tool_input}
    answer, note = _ask(card, tool_name or "tool")
    if answer == _ALLOW_ALL:
        _remember(conv_id, _ALL_RULE)
        return {"behavior": "allow", "updatedInput": tool_input}
    if answer == _ALLOW_ALWAYS:
        _remember(conv_id, rule)
        return {"behavior": "allow", "updatedInput": tool_input}
    if answer == _ALLOW_ONCE:
        return {"behavior": "allow", "updatedInput": tool_input}
    if answer == _DENY:
        message = "The user denied this in the approval card. Don't retry it; ask what to do instead."
        if note:
            message = f"The user denied this and said: {note}"
        return {"behavior": "deny", "message": message}
    return {"behavior": "deny", "message": f"No approval: {answer}"}


@mcp.tool()
def ducky_permission_prompt(tool_name: str, input: dict[str, Any] | None = None, tool_use_id: str = "") -> str:  # noqa: A002 - Claude Code's field name
    """Claude Code permission hook: shows an Allow/Deny card in the chat. Agents never call it.

    Claude Code (``--permission-prompt-tool``) calls this when a tool needs approval and
    reads back ``{"behavior": "allow", "updatedInput": {...}}`` or
    ``{"behavior": "deny", "message": "..."}``.
    """
    del tool_use_id
    result = decide(tool_name, input or {}, conv_id=_conv_id(), project_root=_project_root())
    return json.dumps(result, ensure_ascii=False)
