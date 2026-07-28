"""Shared PTY launch helper for CLI coding agents (Claude Code, Codex, cursor-agent)."""

from __future__ import annotations

import os
import shlex
import time
from typing import Any, Callable

from backend.agent.coding_agents.base import CodingAgentLaunchResult


PushFn = Callable[[dict[str, Any]], None]


def _shell_quote_windows(arg: str) -> str:
    if not arg:
        return '""'
    if all(c.isalnum() or c in "@%_-+=:,./\\" for c in arg):
        return arg
    return '"' + arg.replace('"', '\\"') + '"'


def build_shell_command(argv: list[str], shell: str = "powershell") -> str:
    if shell == "bash":
        return " ".join(shlex.quote(a) for a in argv)
    return " ".join(_shell_quote_windows(a) for a in argv)


def extract_reply_from_output(output: str, *, max_chars: int = 12000) -> str:
    text = (output or "").strip()
    if not text:
        return ""
    # Prefer the last substantial non-prompt chunk.
    lines = [ln.rstrip() for ln in text.splitlines()]
    # Drop common shell prompt noise
    cleaned: list[str] = []
    for ln in lines:
        s = ln.strip()
        if not s:
            cleaned.append("")
            continue
        if s.endswith(">") and len(s) < 40:
            continue
        if s.startswith("PS ") and s.endswith(">"):
            continue
        cleaned.append(ln)
    joined = "\n".join(cleaned).strip()
    if len(joined) > max_chars:
        return joined[-max_chars:]
    return joined


def run_cli_in_terminal(
    *,
    argv: list[str],
    cwd: str,
    conv_id: str,
    title: str,
    env: dict[str, str],
    push: PushFn | None,
    timeout_s: float = 600.0,
    skip_approval: bool = True,
) -> CodingAgentLaunchResult:
    """Open a panel terminal, run argv, wait for completion, return output."""
    from frontend.ui_web.terminal import get_terminal_manager

    mgr = get_terminal_manager()
    workdir = (cwd or "").strip() or os.getcwd()
    if not os.path.isdir(workdir):
        workdir = os.getcwd()

    spawn = mgr.spawn(
        shell="powershell",
        cwd=workdir,
        title=title,
        push_open=True,
        conv_id=conv_id,
    )
    if not spawn.get("ok"):
        return CodingAgentLaunchResult(ok=False, error=str(spawn.get("error") or "failed to open terminal"))

    # TerminalSession.to_dict() exposes session_id (not id).
    session_id = str(spawn.get("session_id") or spawn.get("id") or "").strip()
    if not session_id:
        return CodingAgentLaunchResult(
            ok=False,
            error=f"terminal session missing id (spawn keys: {sorted(spawn.keys())})",
        )

    # Apply env for this shell session (PowerShell). Values are flattened to one
    # line: a literal newline inside the single-line command would split it and
    # execute garbage (multi-line system prompts used to do exactly that).
    env_prefix_parts: list[str] = []
    for k, v in env.items():
        if not k:
            continue
        flat = " ".join(str(v).splitlines())
        env_prefix_parts.append(f"$env:{k}={_shell_quote_windows(flat)}")
    env_prefix = "; ".join(env_prefix_parts)
    cmd = build_shell_command(argv, shell="powershell")
    full_cmd = f"{env_prefix}; {cmd}" if env_prefix else cmd

    if push:
        try:
            push(
                {
                    "type": "status",
                    "text": f"Launching {title}…",
                    "conv_id": conv_id,
                }
            )
        except Exception:
            pass

    if skip_approval:
        session = mgr.get_session(session_id)
        if session is None:
            return CodingAgentLaunchResult(
                ok=False,
                terminal_session_id=session_id,
                error="terminal session disappeared",
            )
        # Run without the Allow/Deny popup — user explicitly chose this coding agent.
        threading_result: dict[str, Any] = {}

        def _run() -> None:
            threading_result["run"] = session.run_command(full_cmd, background=False, timeout_s=timeout_s)

        import threading

        t = threading.Thread(target=_run, daemon=True, name=f"coding-agent-{session_id[:8]}")
        t.start()
        deadline = time.time() + max(30.0, float(timeout_s))
        while t.is_alive() and time.time() < deadline:
            time.sleep(0.2)
        if t.is_alive():
            output = session.read_output_tail(16000)
            return CodingAgentLaunchResult(
                ok=False,
                terminal_session_id=session_id,
                output_tail=output,
                reply_text=extract_reply_from_output(output),
                error="coding agent timed out",
                status="timeout",
            )
        output = session.read_output_tail(16000)
        reply = extract_reply_from_output(output)
        run_info = threading_result.get("run") or {}
        ok = bool(run_info.get("ok", True)) if isinstance(run_info, dict) else True
        return CodingAgentLaunchResult(
            ok=ok or bool(reply),
            terminal_session_id=session_id,
            output_tail=output,
            reply_text=reply,
            status="done" if (ok or reply) else "error",
            error="" if (ok or reply) else str((run_info or {}).get("error") or "agent exited with error"),
        )

    # Fallback: approval path
    result = mgr.run_agent_command(
        session_id,
        full_cmd,
        source="coding_agent",
        conv_id=conv_id,
        background=False,
        wait=True,
        approval_timeout_s=120.0,
        command_timeout_s=timeout_s,
    )
    output = str(result.get("output_tail") or "")
    reply = extract_reply_from_output(output)
    ok = bool(result.get("ok"))
    return CodingAgentLaunchResult(
        ok=ok or bool(reply),
        terminal_session_id=session_id,
        output_tail=output,
        reply_text=reply,
        status="done" if (ok or reply) else "error",
        error="" if (ok or reply) else str(result.get("error") or "agent failed"),
    )
