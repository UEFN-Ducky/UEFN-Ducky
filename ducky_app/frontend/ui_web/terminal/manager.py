"""Thread-safe terminal session manager with agent command approval."""

from __future__ import annotations

import os
import threading
import time
import uuid
from typing import Any, Callable

from frontend.settings import PanelSettings
from frontend.ui_web.terminal.bridge import TerminalBridge
from frontend.ui_web.terminal.session import PendingCommand, TerminalSession
from frontend.ui_web.terminal.shells import shell_label

_APPROVAL_TIMEOUT_S = 120.0
_manager: "TerminalManager | None" = None
_manager_lock = threading.Lock()


def _default_cwd() -> str:
    root = PanelSettings.load().uefn_project_root.strip()
    return root or "."


class TerminalManager:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._sessions: dict[str, _SessionEntry] = {}
        self._pending: dict[str, PendingCommand] = {}
        self._push: Callable[[dict[str, Any]], None] | None = None

    def set_push(self, push: Callable[[dict[str, Any]], None] | None) -> None:
        self._push = push

    def _emit(self, event: dict[str, Any]) -> None:
        push = self._push
        if push:
            try:
                push(event)
            except Exception:
                pass

    def spawn(
        self,
        shell: str = "bash",
        cwd: str | None = None,
        title: str = "",
        *,
        push_open: bool = False,
        conv_id: str = "",
    ) -> dict[str, Any]:
        shell_norm = shell_label(shell)
        workdir = (cwd or _default_cwd()).strip() or "."
        if not os.path.isdir(workdir):
            workdir = os.getcwd()
        entry: _SessionEntry | None = None
        with self._lock:
            session = TerminalSession(shell=shell_norm, cwd=workdir, title=title)  # type: ignore[arg-type]
            bridge = TerminalBridge(
                on_input=lambda data, s=session: self._user_write(s.id, data),
                on_resize=lambda c, r, s=session: s.resize(c, r),
                get_replay=lambda s=session: s.read_output_tail(16000),
                get_status=lambda s=session: self._session_status(s),
            )
            bridge.start()
            session.port = bridge.port
            session.ws_url = bridge.ws_url

            def on_output(text: str, b=bridge) -> None:
                b.push_output(text)

            def on_exit(code: int, b=bridge) -> None:
                b.push_exit(code)

            session._on_output = on_output  # type: ignore[attr-defined]
            session._on_exit = on_exit  # type: ignore[attr-defined]
            shell_fallback = False
            try:
                session.spawn()
            except Exception as exc:
                if shell_norm == "bash":
                    session.shell = "powershell"
                    shell_norm = "powershell"
                    shell_fallback = True
                    try:
                        session.spawn()
                    except Exception as exc2:
                        bridge.stop()
                        return {"ok": False, "error": str(exc2), "hint": f"bash failed ({exc}); powershell also failed"}
                else:
                    bridge.stop()
                    return {"ok": False, "error": str(exc)}
            entry = _SessionEntry(session=session, bridge=bridge)
            self._sessions[session.id] = entry

        result = {
            "ok": True,
            **session.to_dict(),
            "tab_id": f"terminal:{session.id}",
            "shell_fallback": shell_fallback,
        }
        if push_open:
            self._emit(
                {
                    "type": "terminal_open",
                    "session_id": session.id,
                    "shell": session.shell,
                    "title": session.title,
                    "cwd": session.cwd,
                    "ws_url": session.ws_url,
                    "conv_id": conv_id,
                }
            )
        return result

    def busy_state(self, session_id: str) -> dict[str, Any]:
        """Whether a command (agent or user-typed) is running in the session."""
        session = self.get_session(session_id)
        if not session:
            return {"ok": False, "error": "session not found"}
        return {
            "ok": True,
            "session_id": session_id,
            "busy": session.is_busy(),
            "running": session.has_running_command(),
        }

    def kill(self, session_id: str, *, push_close: bool = True) -> dict[str, Any]:
        entry = self._pop_session(session_id)
        if entry is None:
            return {"ok": False, "error": "session not found"}
        if push_close:
            self._emit({"type": "terminal_close", "session_id": session_id})
        return {"ok": True, "session_id": session_id}

    def list_sessions(self) -> list[dict[str, Any]]:
        with self._lock:
            return [e.session.to_dict() for e in self._sessions.values()]

    def get_session(self, session_id: str) -> TerminalSession | None:
        with self._lock:
            entry = self._sessions.get(session_id)
            return entry.session if entry else None

    def _session_status(self, session: TerminalSession) -> dict[str, Any]:
        alive = session.is_alive()
        exit_code = session._exit_code
        return {
            "alive": alive,
            "exit_code": exit_code,
        }

    def _user_write(self, session_id: str, data: str) -> None:
        session = self.get_session(session_id)
        if not session:
            return
        if not session.is_alive():
            with self._lock:
                entry = self._sessions.get(session_id)
            if entry:
                entry.bridge.push_status({"alive": False, "exit_code": session._exit_code})
            return
        try:
            session.write(data)
        except Exception:
            pass

    def write(self, session_id: str, data: str) -> dict[str, Any]:
        session = self.get_session(session_id)
        if not session:
            return {"ok": False, "error": "session not found"}
        try:
            session.write(data)
            return {"ok": True}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    def resize(self, session_id: str, cols: int, rows: int) -> dict[str, Any]:
        session = self.get_session(session_id)
        if not session:
            return {"ok": False, "error": "session not found"}
        session.resize(cols, rows)
        return {"ok": True}

    def request_command(
        self,
        session_id: str,
        command: str,
        source: str = "",
        conv_id: str = "",
        *,
        background: bool = False,
        push_pending: bool = True,
    ) -> dict[str, Any]:
        cmd = (command or "").strip()
        if not cmd:
            return {"ok": False, "error": "command must not be empty"}
        session = self.get_session(session_id)
        if not session:
            return {"ok": False, "error": "session not found"}
        if session.is_busy() and not background:
            return {
                "ok": False,
                "error": "session busy",
                "hint": "Wait for the current command or open another terminal with ducky_terminal_open.",
            }
        request_id = uuid.uuid4().hex[:12]
        pending = PendingCommand(
            request_id=request_id,
            session_id=session_id,
            command=cmd,
            source=source or "agent",
            conv_id=conv_id,
            background=background,
        )
        with self._lock:
            self._pending[request_id] = pending
        if push_pending:
            self._emit(
                {
                    "type": "terminal_command_pending",
                    "request_id": request_id,
                    "session_id": session_id,
                    "command": cmd,
                    "shell": session.shell,
                    "cwd": session.cwd,
                    "conv_id": conv_id,
                    "source": source or "ducky_terminal_run",
                }
            )
        return {"ok": True, "request_id": request_id, "status": "pending_approval"}

    def approve_command(self, request_id: str) -> dict[str, Any]:
        pending = self._take_pending(request_id)
        if pending is None:
            return {"ok": False, "error": "request not found or already decided"}
        session = self.get_session(pending.session_id)
        if not session:
            pending.approved = False
            pending.rejection_reason = "session not found"
            pending.decided.set()
            return {"ok": False, "error": "session not found"}
        pending.approved = True
        pending.decided.set()
        if pending.background:
            result = session.run_command(pending.command, background=True)
        else:
            # Run after signaling approval so agent waiters unblock; avoid blocking UI on completion.
            threading.Thread(
                target=session.run_command,
                args=(pending.command,),
                kwargs={"background": False, "timeout_s": 300.0},
                daemon=True,
                name=f"terminal-run-{request_id}",
            ).start()
            result = {"ok": True, "status": "running"}
        return {"ok": True, "request_id": request_id, "run": result}

    def reject_command(self, request_id: str, reason: str = "rejected by user") -> dict[str, Any]:
        pending = self._take_pending(request_id)
        if pending is None:
            return {"ok": False, "error": "request not found or already decided"}
        pending.approved = False
        pending.rejection_reason = reason
        pending.decided.set()
        return {"ok": True, "request_id": request_id, "approved": False, "reason": reason}

    def wait_for_approval(self, request_id: str, timeout_s: float = _APPROVAL_TIMEOUT_S) -> dict[str, Any]:
        with self._lock:
            pending = self._pending.get(request_id)
        if pending is None:
            return {"ok": False, "error": "request not found"}
        if not pending.decided.wait(timeout=max(1.0, float(timeout_s))):
            self.reject_command(request_id, reason="command not approved (timed out)")
            return {"ok": False, "error": "command not approved (timed out)"}
        if not pending.approved:
            return {"ok": False, "error": pending.rejection_reason or "command rejected by user"}
        with self._lock:
            self._pending.pop(request_id, None)
        return {"ok": True, "approved": True}

    def run_agent_command(
        self,
        session_id: str,
        command: str,
        *,
        source: str = "",
        conv_id: str = "",
        background: bool = False,
        wait: bool = True,
        approval_timeout_s: float = _APPROVAL_TIMEOUT_S,
        command_timeout_s: float = 300.0,
    ) -> dict[str, Any]:
        req = self.request_command(
            session_id,
            command,
            source=source,
            conv_id=conv_id,
            background=background,
        )
        if not req.get("ok"):
            return req
        request_id = str(req["request_id"])
        approval = self.wait_for_approval(request_id, timeout_s=approval_timeout_s)
        if not approval.get("ok"):
            return approval
        session = self.get_session(session_id)
        if session is None:
            return {"ok": False, "error": "session not found"}
        if not wait or background:
            return {
                "ok": True,
                "request_id": request_id,
                "session_id": session_id,
                "status": "running",
            }
        limit = float(command_timeout_s)
        deadline = (time.time() + max(1.0, limit)) if limit > 0 else None
        while session.is_busy() and (deadline is None or time.time() < deadline):
            time.sleep(0.1)
        if session.is_busy():
            return {
                "ok": False,
                "error": "command timed out",
                "output_tail": session.read_output_tail(),
                "request_id": request_id,
                "session_id": session_id,
            }
        return {
            "ok": True,
            "request_id": request_id,
            "session_id": session_id,
            "output_tail": session.read_output_tail(),
        }

    def read_output(self, session_id: str, max_chars: int = 8000) -> dict[str, Any]:
        session = self.get_session(session_id)
        if not session:
            return {"ok": False, "error": "session not found"}
        return {
            "ok": True,
            "session_id": session_id,
            "output": session.read_output_tail(max_chars=max_chars),
            "busy": session.is_busy(),
            "alive": session.is_alive(),
        }

    def shutdown_all(self) -> None:
        with self._lock:
            ids = list(self._sessions.keys())
            pending_ids = list(self._pending.keys())
        for rid in pending_ids:
            self.reject_command(rid, reason="panel shutting down")
        for sid in ids:
            self.kill(sid, push_close=False)

    def _take_pending(self, request_id: str) -> PendingCommand | None:
        with self._lock:
            return self._pending.pop(request_id, None)

    def _pop_session(self, session_id: str) -> "_SessionEntry | None":
        with self._lock:
            entry = self._sessions.pop(session_id, None)
        if entry is None:
            return None
        entry.bridge.stop()
        entry.session.kill()
        return entry


class _SessionEntry:
    __slots__ = ("session", "bridge")

    def __init__(self, *, session: TerminalSession, bridge: TerminalBridge) -> None:
        self.session = session
        self.bridge = bridge


def get_terminal_manager() -> TerminalManager:
    global _manager
    with _manager_lock:
        if _manager is None:
            _manager = TerminalManager()
        return _manager
