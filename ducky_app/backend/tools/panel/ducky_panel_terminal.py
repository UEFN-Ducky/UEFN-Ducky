"""Panel terminal tools: open, run, read and close the Ducky window's terminals."""

from __future__ import annotations

import json
import re

from backend.server import mcp
from backend.util.json_util import tool_json
from frontend.settings import PANEL_LISTENER_PORT


class _PanelTerminalManager:
    """Outside agents must use the terminals owned by the visible window."""

    def _call(self, method: str, args: dict, timeout: float = 30.0) -> dict:
        import urllib.request

        request = urllib.request.Request(
            f"http://127.0.0.1:{PANEL_LISTENER_PORT - 1}/__panel_api/{method}",
            data=json.dumps({"args": args}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except (OSError, ValueError) as exc:
            return {"ok": False, "error": f"Ducky window terminal unavailable: {exc}"}
        if not isinstance(payload, dict):
            return {"ok": False, "error": "Invalid Ducky window terminal response"}
        result = payload.get("result", payload)
        return result if isinstance(result, dict) else {"ok": False, "error": "Invalid terminal result"}

    def spawn(self, **kwargs) -> dict:
        # ponytail: the window's terminal_spawn has no from_agent yet, so a chat-less
        # terminal opened from here isn't run_owned; add it there to close those too.
        kwargs.pop("from_agent", None)
        return self._call("terminal_spawn", {**kwargs, "cwd": kwargs.get("cwd") or ""})

    def get_session(self, session_id: str):
        from types import SimpleNamespace

        for session in self.list_sessions():
            if session.get("session_id") == session_id:
                return SimpleNamespace(**session)
        return None

    def list_sessions(self) -> list:
        result = self._call("terminal_list", {})
        if "error" in result:
            raise RuntimeError(result["error"])
        return result.get("sessions", [])

    def run_agent_command(self, session_id: str, command: str, **kwargs) -> dict:
        # The window waits for approval and, for foreground runs, completion.
        # Do not impose the usual short panel HTTP timeout on those waits.
        timeout = kwargs["approval_timeout_s"] + 30.0
        if kwargs.get("wait", True) and not kwargs.get("background", False):
            timeout += kwargs["command_timeout_s"]
        return self._call(
            "terminal_request_command",
            {"session_id": session_id, "command": command, "agent_run": True, **kwargs},
            timeout=timeout,
        )

    def read_output(self, session_id: str, max_chars: int = 8000) -> dict:
        return self._call("terminal_read_output", {"session_id": session_id, "max_chars": max_chars})

    def kill(self, session_id: str, **_kwargs) -> dict:
        return self._call("terminal_kill", {"session_id": session_id})

    def hold(self, session_id: str):
        # ponytail: a tool server that dies mid-run leaves the hold; the terminal then stays
        # until the person closes it or Ducky restarts.
        self._call("terminal_hold", {"session_id": session_id})
        return lambda: self._call("terminal_let_go", {"session_id": session_id})


def _terminal_manager():
    from frontend.ui_web.agent_modes import get_panel_push

    push = get_panel_push()
    if push is None:
        return _PanelTerminalManager()
    from frontend.ui_web.terminal import get_terminal_manager

    mgr = get_terminal_manager()
    mgr.set_push(push)
    return mgr


@mcp.tool()
def ducky_terminal_open(
    shell: str = "bash",
    cwd: str = "",
    title: str = "",
    conv_id: str = "",
    pretty: bool = False,
) -> str:
    """Open an integrated terminal (bash or powershell) in the Ducky panel. It shows in the
    header's terminal list, never as a tab: the person stays on the chat they are reading.

    In a chat, an idle terminal it already opened with the same shell and folder is
    used again instead of a new one (the result says reused=true). Close it with
    ducky_terminal_close when you are done with it; Ducky closes a chat's terminal itself
    about a minute after that chat's agent finishes (unless a command still runs in it)."""
    mgr = _terminal_manager()
    result = mgr.spawn(
        shell=shell, cwd=cwd or None, title=title, push_open=True,
        conv_id=_terminal_chat(conv_id), reuse_idle=True, activate=False, from_agent=True,
    )
    return tool_json(result, pretty=pretty)


@mcp.tool()
def ducky_terminal_run(
    session_id: str,
    command: str,
    conv_id: str = "",
    wait: bool = True,
    background: bool = False,
    approval_timeout_s: float = 120.0,
    command_timeout_s: float = 300.0,
    expect_minutes: float = 0,
    pretty: bool = False,
) -> str:
    """Run a shell command in a panel terminal after the user approves it on the Allow/Deny
    card in this chat (no card when this chat said "Allow everything" or "Always allow" for
    this command, or when the command is typed into the Local workflow running it).

    expect_minutes: how long this run should take; shows the person a progress bar."""
    mgr = _terminal_manager()
    chat = _terminal_chat(conv_id)
    # The session lookup is a window round trip (terminal_list): only for a release script.
    busy = (release_refusal(command, _session_cwd(mgr, session_id.strip()))
            if _RELEASE_SCRIPT.search(command or "") else "")
    if busy:
        return tool_json({"ok": False, "error": busy, "session_id": session_id.strip()}, pretty=pretty)
    from backend.automations.runner import typed_command_approved
    from backend.tools.panel.permission_prompt import allows_everything

    auto = bool(chat and allows_everything(chat)) or typed_command_approved(command)
    # The manager's steer check comes from conv_id (the chat's steer inbox), in this
    # process or in the window's: a person's message returns the run early.
    result = mgr.run_agent_command(
        session_id.strip(),
        command,
        source="ducky_terminal_run",
        conv_id=chat,
        background=background,
        wait=wait,
        approval_timeout_s=max(5.0, min(float(approval_timeout_s), 600.0)),
        command_timeout_s=max(5.0, min(float(command_timeout_s), 3600.0)),
        auto_approve=auto,
        expect_minutes=max(0.0, float(expect_minutes or 0)),
    )
    if not auto and "not approved" in str(result.get("error") or ""):
        # Say whose card it was: a team whose chats allowed everything waited on cards
        # nobody saw, and the bare "timed out" didn't show why (Oct 12 2026).
        result["asked_in_chat"] = chat or "(no chat: the call carried no chat identity)"
        result["chat_allows_everything"] = bool(chat) and allows_everything(chat)
    return tool_json(early_return_hint(result), pretty=pretty)


_RELEASE_SCRIPT = re.compile(r"""([^\s"'&|;]*?)[\\/]?\brelease\.(?:py|sh)\b""", re.I)


def _session_cwd(mgr, session_id: str) -> str:
    try:
        return str(getattr(mgr.get_session(session_id), "cwd", "") or "")
    except Exception:  # noqa: BLE001 - no session info: the command's own path still counts
        return ""


def _plugin_id_near(folder: str) -> str:
    """The id in the plugin.json at ``folder`` or up to two folders above it (scripts/release.py)."""
    from pathlib import Path

    if not folder:
        return ""
    here = Path(folder)
    for candidate in (here, *list(here.parents)[:2]):
        manifest = candidate / "plugin.json"
        try:
            return str(json.loads(manifest.read_text(encoding="utf-8")).get("id") or "").strip()
        except (OSError, ValueError, AttributeError):
            continue
    return ""


def release_refusal(command: str, cwd: str) -> str:
    """Why a plugin repo's release.py / release.sh must not run now: another release workflow
    holds that plugin (L2's release lock). A workflow running its own release passes."""
    match = _RELEASE_SCRIPT.search(command or "")
    if not match:
        return ""
    from pathlib import Path

    script_dir = match.group(1).strip("\"'")
    if script_dir and cwd and not Path(script_dir).is_absolute():
        script_dir = str(Path(cwd) / script_dir)
    plugin = _plugin_id_near(script_dir) or _plugin_id_near(cwd)
    if not plugin:
        return ""
    from backend.automations.runner import _REGISTRY_RUNS
    from backend.tools.panel.panel_ai_plugins import release_busy

    busy = release_busy(plugin)
    if busy and any(not release_busy(plugin, run_id=run) for _wid, run in _REGISTRY_RUNS.get()):
        busy = ""  # this command is that release workflow's own step
    return f"Not run: {plugin}: {busy}. Wait for it to finish (or stop it), then run the release again." if busy else ""


def early_return_hint(result: dict) -> dict:
    """A run that came back early (still_running / waiting_for_approval) stays as the manager
    sent it, plus the exact call that reads the rest of its output."""
    if isinstance(result, dict) and (result.get("still_running") or result.get("waiting_for_approval")):
        result.setdefault("read_with", f'ducky_terminal_read_output(session_id="{result.get("session_id", "")}")')
    return result


def _terminal_chat(conv_id: str) -> str:
    """The chat running this tool: the run's own (bound) chat, else the one it named, else
    the dedicated tool server's chat (DUCKY_CONV_ID). Empty: no identity at all, and the
    manager treats the terminal as run-owned (from_agent)."""
    import os

    try:
        from backend.workspace import identity as run_identity

        ctx = run_identity.current()
        bound = ((ctx.conv_id if ctx is not None else "") or "").strip()
        if bound:
            return bound
    except Exception:
        pass
    return (conv_id or "").strip() or (os.environ.get("DUCKY_CONV_ID") or "").strip()


@mcp.tool()
def ducky_terminal_read_output(session_id: str, max_chars: int = 8000, pretty: bool = False) -> str:
    """Read recent output from a terminal session (for wait=false / parallel workflows)."""
    from frontend.ui_web.terminal.session import plain_terminal_text

    mgr = _terminal_manager()
    max_chars = max(500, min(int(max_chars), 32000))
    result = mgr.read_output(session_id.strip(), max_chars=max_chars)
    if isinstance(result.get("output"), str):
        # Plain text, as an agent reads it: no escape codes, progress redraws resolved.
        result["output"] = plain_terminal_text(result["output"])
    return tool_json(result, pretty=pretty)


@mcp.tool()
def ducky_terminal_list(pretty: bool = False) -> str:
    """List active integrated terminal sessions in the Ducky panel."""
    mgr = _terminal_manager()
    return tool_json({"sessions": mgr.list_sessions()}, pretty=pretty)


@mcp.tool()
def ducky_terminal_close(session_id: str, pretty: bool = False) -> str:
    """Close a terminal session and its tab in the Ducky panel."""
    mgr = _terminal_manager()
    return tool_json(mgr.kill(session_id.strip(), push_close=True), pretty=pretty)
