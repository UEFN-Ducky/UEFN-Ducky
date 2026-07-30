"""Single PTY terminal session."""

from __future__ import annotations

import re
import threading
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Callable

from frontend.ui_web.terminal.shells import TerminalShell, resolve_shell, shell_label

_OUTPUT_RING_MAX = 400
_DONE_RE = re.compile(r"__DUCKY_DONE__(\d+)__")
# Escape sequences that ask the terminal to REPLY (device attributes ESC[c,
# status reports ESC[5n/6n, window/cell size reports ESC[14t…21t). Replaying
# them makes xterm.js answer again, and the answer reaches the shell as typed
# input — bash then "runs" junk like `1;2c`. Strip them from replay text only;
# the live stream still passes them through for real terminal negotiation.
_QUERY_SEQ_RE = re.compile(
    r"\x1b\[[?>=]?[0-9;]*[cn]"
    r"|\x1b\[(?:1[4689]|2[01])(?:;[0-9;]*)?t"
)
_APPROVAL_TIMEOUT_S = 120.0


def _process_snapshot() -> tuple[dict[int, list[int]], dict[int, str]]:
    """One Toolhelp snapshot → ({parent pid: [child pids]}, {pid: exe name})."""
    import ctypes
    from ctypes import wintypes

    class PROCESSENTRY32W(ctypes.Structure):
        _fields_ = [
            ("dwSize", wintypes.DWORD),
            ("cntUsage", wintypes.DWORD),
            ("th32ProcessID", wintypes.DWORD),
            ("th32DefaultHeapID", ctypes.c_size_t),
            ("th32ModuleID", wintypes.DWORD),
            ("cntThreads", wintypes.DWORD),
            ("th32ParentProcessID", wintypes.DWORD),
            ("pcPriClassBase", ctypes.c_long),
            ("dwFlags", wintypes.DWORD),
            ("szExeFile", ctypes.c_wchar * 260),
        ]

    TH32CS_SNAPPROCESS = 0x2
    kernel32 = ctypes.windll.kernel32
    snapshot = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    children: dict[int, list[int]] = {}
    names: dict[int, str] = {}
    if snapshot == ctypes.c_void_p(-1).value:
        return children, names
    try:
        entry = PROCESSENTRY32W()
        entry.dwSize = ctypes.sizeof(PROCESSENTRY32W)
        ok = kernel32.Process32FirstW(snapshot, ctypes.byref(entry))
        while ok:
            pid = int(entry.th32ProcessID)
            children.setdefault(int(entry.th32ParentProcessID), []).append(pid)
            names[pid] = entry.szExeFile.lower()
            ok = kernel32.Process32NextW(snapshot, ctypes.byref(entry))
    finally:
        kernel32.CloseHandle(snapshot)
    return children, names


def shell_has_foreground_child(shell_pid: int) -> bool:
    """True when the shell's process tree extends beyond the interactive shell.

    Git's ``bin\\bash.exe`` is a shim whose one child is the real bash, so a
    single SAME-NAMED child is collapsed before deciding: idle bash is
    shim→bash (not busy); any other descendant means a command is running.
    """
    children, names = _process_snapshot()
    pid = int(shell_pid)
    for _ in range(8):  # bounded walk, shim chains are shallow
        kids = children.get(pid, [])
        if not kids:
            return False
        if len(kids) == 1 and names.get(kids[0]) == names.get(pid):
            pid = kids[0]
            continue
        return True
    return True


def kill_process_tree(pid: int) -> None:
    """Force-kill ``pid`` and every descendant (running command + its children)."""
    import subprocess

    try:
        subprocess.run(
            ["taskkill", "/F", "/T", "/PID", str(int(pid))],
            capture_output=True,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            timeout=10.0,
        )
    except Exception:
        pass


def normalize_exit_code(code: int) -> int:
    """Map Windows NTSTATUS-style exits to familiar Unix-style codes for display."""
    if code == 0:
        return 0
    unsigned = code & 0xFFFFFFFF
    if unsigned == 0xC000013A:  # STATUS_CONTROL_C_EXIT
        return 130
    if unsigned >= 0xC0000000:
        return 1
    return code


@dataclass
class PendingCommand:
    request_id: str
    session_id: str
    command: str
    source: str
    conv_id: str
    background: bool
    created_at: float = field(default_factory=time.time)
    decided: threading.Event = field(default_factory=threading.Event)
    approved: bool = False
    rejection_reason: str = ""


class TerminalSession:
    """Wraps one pywinpty process with output ring and agent command tracking."""

    def __init__(
        self,
        *,
        shell: TerminalShell,
        cwd: str,
        title: str = "",
        on_output: Callable[[str], None] | None = None,
        on_exit: Callable[[int], None] | None = None,
    ) -> None:
        self.id = uuid.uuid4().hex[:12]
        self.shell = shell_label(shell)
        self.cwd = cwd
        self.title = (title or f"{self.shell}").strip()[:80]
        self.port = 0
        self.ws_url = ""
        self._on_output = on_output
        self._on_exit = on_exit
        self._pty: Any = None
        self._read_thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._write_lock = threading.Lock()
        self._output_ring: deque[str] = deque(maxlen=_OUTPUT_RING_MAX)
        self._output_cond = threading.Condition()
        self._cols = 120
        self._rows = 30
        self._busy = False
        self._busy_lock = threading.Lock()
        self._exit_code: int | None = None
        self._error = ""
        self._pending_done: threading.Event | None = None
        self._pending_exit_code: int | None = None

    def spawn(self) -> None:
        import os

        from winpty import PtyProcess

        from frontend.ui_web.terminal.path_env import env_with_fresh_path

        if not os.path.isdir(self.cwd):
            from frontend.settings import PanelSettings

            fallback = PanelSettings.load().uefn_project_root.strip() or os.getcwd()
            self.cwd = fallback if os.path.isdir(fallback) else os.getcwd()

        _exe, argv = resolve_shell(self.shell)
        # pywinpty expects argv list — list2cmdline breaks Git Bash on Windows.
        # Refresh Path from the registry so installs added after Ducky launched
        # (e.g. Claude Code in %USERPROFILE%\.local\bin) are visible.
        self._pty = PtyProcess.spawn(
            argv,
            cwd=self.cwd,
            env=env_with_fresh_path(),
            dimensions=(self._rows, self._cols),
        )
        self._stop.clear()
        self._read_thread = threading.Thread(target=self._read_loop, daemon=True, name=f"pty-{self.id}")
        self._read_thread.start()
        # No prompt "kick": both shells print their first prompt unprompted, and
        # the output ring replays it to clients that attach late. Kicks raced the
        # shell's own startup and stacked 2-3 prompts per new terminal.

    def _read_loop(self) -> None:
        pty = self._pty
        if pty is None:
            return
        while not self._stop.is_set():
            try:
                if not pty.isalive():
                    break
                chunk = pty.read(4096)
            except Exception:
                break
            if not chunk:
                time.sleep(0.02)
                continue
            text = chunk.decode("utf-8", errors="replace") if isinstance(chunk, bytes) else str(chunk)
            if not text:
                continue
            with self._output_cond:
                self._output_ring.append(text)
                self._output_cond.notify_all()
            if self._on_output:
                try:
                    self._on_output(text)
                except Exception:
                    pass
            match = _DONE_RE.search(text)
            if match and self._pending_done is not None:
                try:
                    self._pending_exit_code = int(match.group(1))
                except ValueError:
                    self._pending_exit_code = 0
                self._pending_done.set()
        try:
            code = int(getattr(pty, "exitstatus", 1) or 1) if pty is not None else 1
        except Exception:
            code = 1
        self._exit_code = normalize_exit_code(code)
        with self._busy_lock:
            self._busy = False
        if self._pending_done is not None and not self._pending_done.is_set():
            self._pending_exit_code = code
            self._pending_done.set()
        if self._on_exit:
            try:
                self._on_exit(code)
            except Exception:
                pass

    def is_alive(self) -> bool:
        pty = self._pty
        if pty is None:
            return False
        try:
            return bool(pty.isalive())
        except Exception:
            return False

    def has_running_command(self) -> bool:
        """True when an agent command is pending or the user has a command running."""
        if self.is_busy():
            return True
        pty = self._pty
        pid = getattr(pty, "pid", None) if pty is not None else None
        if not pid or not self.is_alive():
            return False
        try:
            return shell_has_foreground_child(int(pid))
        except Exception:
            return False

    def write(self, data: str) -> None:
        pty = self._pty
        if pty is None or not self.is_alive():
            raise RuntimeError("terminal session not running")
        with self._write_lock:
            pty.write(data)

    def resize(self, cols: int, rows: int) -> None:
        self._cols = max(20, min(int(cols), 500))
        self._rows = max(5, min(int(rows), 200))
        pty = self._pty
        if pty is None:
            return
        try:
            pty.setwinsize(self._rows, self._cols)
        except Exception:
            pass

    def kill(self) -> None:
        self._stop.set()
        pty = self._pty
        self._pty = None
        if pty is not None:
            pid = getattr(pty, "pid", None)
            try:
                if pty.isalive():
                    # Tree-kill first: closing only the ConPTY can orphan a
                    # running command's children (dev servers, watchers, …).
                    if pid:
                        kill_process_tree(int(pid))
                    pty.close(force=True)
            except Exception:
                pass
        if self._pending_done is not None and not self._pending_done.is_set():
            self._pending_exit_code = -1
            self._pending_done.set()

    def is_busy(self) -> bool:
        with self._busy_lock:
            return self._busy

    def set_busy(self, busy: bool) -> None:
        with self._busy_lock:
            self._busy = busy

    def read_output_tail(self, max_chars: int = 8000) -> str:
        with self._output_cond:
            text = "".join(self._output_ring)
        text = _QUERY_SEQ_RE.sub("", text)
        if len(text) > max_chars:
            return text[-max_chars:]
        return text

    def _wrap_agent_command(self, command: str, background: bool) -> str:
        cmd = command.rstrip("\r\n")
        if background:
            if self.shell == "powershell":
                return f"{cmd}\r\n"
            return f"({cmd}) &\r\n"
        if self.shell == "powershell":
            return f"{cmd}; Write-Output \"__DUCKY_DONE__$LASTEXITCODE__\"\r\n"
        return f"{cmd}; echo __DUCKY_DONE__$?__\r\n"

    def run_command(
        self,
        command: str,
        *,
        background: bool = False,
        timeout_s: float = 300.0,
    ) -> dict[str, Any]:
        if self.is_busy() and not background:
            return {"ok": False, "error": "session busy", "hint": "Wait for the current command or open another terminal."}
        wrapped = self._wrap_agent_command(command, background)
        self.set_busy(not background)
        self._pending_done = None if background else threading.Event()
        self._pending_exit_code = None
        try:
            self.write(wrapped)
        except Exception as exc:
            self.set_busy(False)
            return {"ok": False, "error": str(exc)}
        if background:
            return {"ok": True, "status": "running"}
        done = self._pending_done
        if done is None:
            return {"ok": True, "status": "running"}
        # timeout_s <= 0: wait until the shell reports done (no wall-clock kill).
        wait_timeout = None if float(timeout_s) <= 0 else max(1.0, float(timeout_s))
        if not done.wait(timeout=wait_timeout):
            self.set_busy(False)
            return {
                "ok": False,
                "error": "command timed out",
                "output_tail": self.read_output_tail(),
            }
        self.set_busy(False)
        return {
            "ok": True,
            "exit_code": self._pending_exit_code,
            "output_tail": self.read_output_tail(),
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.id,
            "shell": self.shell,
            "cwd": self.cwd,
            "title": self.title,
            "ws_url": self.ws_url,
            "busy": self.is_busy(),
            "alive": self.is_alive(),
            "exit_code": self._exit_code,
        }
