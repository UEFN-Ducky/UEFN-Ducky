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


def pty_argv(shell: str, spawn_argv: list[str] | None) -> list[str]:
    """Argv for the PTY. A command spawn must not require Git Bash."""
    if spawn_argv:
        return [str(a) for a in spawn_argv]
    _exe, argv = resolve_shell(shell)
    return argv


# Output kept to redraw a terminal shown again: the last ~512 KB, in chunks merged up to 8 KB so a
# stream of tiny writes (pytest prints a progress code per test) can't push the history out.
_OUTPUT_RING_CHARS = 512 * 1024
_OUTPUT_CHUNK_CHARS = 8 * 1024
# Taskbar progress codes (OSC 9;4) never show anything; they stay out of the kept history.
_PROGRESS_SEQ_RE = re.compile(r"\x1b\]9;4;[0-9;]*(?:\x07|\x1b\\)")
_DONE_RE = re.compile(r"__DUCKY_DONE__(-?\d+)__")  # Windows exit codes can be negative
# Printed just before an agent command runs. The typed line only spells it as a format
# string, so the shell's echo of the command (PSReadLine redraws it several times) never
# matches; what lies between this and the done marker is the command's own output.
_BEGIN_MARK = "__DUCKY_BEGIN__"
_ESCAPE_SEQ_RE = re.compile(
    r"\x1b\[[0-?]*[ -/]*[@-~]"  # CSI: colors, cursor moves, mode switches
    r"|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)"  # OSC: titles, hyperlinks, progress
    r"|\x1b[@-Z\\-_]"
)
_AGENT_OUTPUT_CHARS = 8000
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


def plain_terminal_text(raw: str) -> str:
    """Terminal bytes as an agent should read them: no escape codes, redraws resolved."""
    text = _ESCAPE_SEQ_RE.sub("", raw or "").replace("\r\n", "\n")
    lines = []
    for line in text.split("\n"):
        # A bare carriage return redraws the line; what was written last is what shows.
        if "\r" in line:
            line = next((part for part in reversed(line.split("\r")) if part), "")
        lines.append("".join(ch for ch in line if ch == "\t" or ch >= " ").rstrip())
    return "\n".join(lines)


def agent_command_output(raw: str) -> str:
    """The command's own output between the begin and done markers, else the plain tail."""
    text = plain_terminal_text(raw)
    begin = text.rfind(_BEGIN_MARK)
    if begin >= 0:
        # The latest command: a timed-out one has no done marker yet.
        text = text[begin + len(_BEGIN_MARK):]
        done = _DONE_RE.search(text)
    else:
        done = None
        for done in _DONE_RE.finditer(text):
            pass
    if done:
        text = text[:done.start()]
    return text.strip("\n")[-_AGENT_OUTPUT_CHARS:]


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


ProcessSnapshot = Callable[[], tuple[dict[int, list[int]], dict[int, str]]]


def shell_has_foreground_child(shell_pid: int, snapshot: ProcessSnapshot | None = None) -> bool:
    """True when the shell's process tree extends beyond the interactive shell.

    Git's ``bin\\bash.exe`` is a shim whose one child is the real bash, so a
    single SAME-NAMED child is collapsed before deciding: idle bash is
    shim→bash (not busy); any other descendant means a command is running.

    ``snapshot`` lets several shells share one walk of every process on the PC.
    """
    children, names = (snapshot or _process_snapshot)()
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
    # The agent that asked runs it itself and waits for the exit code.
    runner_waits: bool = False
    timeout_s: float = 300.0
    # An Allow pop-up was shown for it (not when the chat allows everything).
    asked: bool = False


class TerminalSession:
    """Wraps one pywinpty process with output ring and agent command tracking."""

    def __init__(
        self,
        *,
        shell: TerminalShell,
        cwd: str,
        title: str = "",
        hidden: bool = False,
        spawn_argv: list[str] | None = None,
        env_extra: dict[str, str] | None = None,
        on_output: Callable[[str], None] | None = None,
        on_exit: Callable[[int], None] | None = None,
    ) -> None:
        self.id = uuid.uuid4().hex[:12]
        self.shell = shell_label(shell)
        self.cwd = cwd
        self.title = (title or f"{self.shell}").strip()[:80]
        self.hidden = bool(hidden)
        self.spawn_argv = [str(a) for a in spawn_argv] if spawn_argv else None
        self.env_extra = {str(k): str(v) for k, v in (env_extra or {}).items() if k}
        self.port = 0
        self.ws_url = ""
        self._on_output = on_output
        self._on_exit = on_exit
        self._pty: Any = None
        self._read_thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._write_lock = threading.Lock()
        self._output_ring: deque[str] = deque()
        self._output_chars = 0
        self._output_cond = threading.Condition()
        self._cols = 120
        self._rows = 30
        self._busy = False
        self._busy_lock = threading.Lock()
        self._exit_code: int | None = None
        self._error = ""
        self._pending_done: threading.Event | None = None
        self._pending_exit_code: int | None = None
        # The end of the last chunk: the done marker can be split across two reads.
        self._done_scan = ""

    def spawn(self) -> None:
        import os

        from winpty import PtyProcess

        from frontend.ui_web.terminal.path_env import env_with_fresh_path

        if not os.path.isdir(self.cwd):
            from frontend.settings import PanelSettings

            fallback = PanelSettings.load().uefn_project_root.strip() or os.getcwd()
            self.cwd = fallback if os.path.isdir(fallback) else os.getcwd()

        argv = pty_argv(self.shell, self.spawn_argv)
        # pywinpty expects argv list — list2cmdline breaks Git Bash on Windows.
        # Refresh Path from the registry so installs added after Ducky launched
        # (e.g. Claude Code in %USERPROFILE%\.local\bin) are visible.
        env = env_with_fresh_path()
        if self.env_extra:
            env.update(self.env_extra)
        self._pty = PtyProcess.spawn(
            argv,
            cwd=self.cwd,
            env=env,
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
                self._keep_output(_PROGRESS_SEQ_RE.sub("", text))
                self._output_cond.notify_all()
            if self._on_output:
                try:
                    self._on_output(text)
                except Exception:
                    pass
            scan = self._done_scan + text
            match = _DONE_RE.search(scan)
            self._done_scan = "" if match else scan[-32:]
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

    def has_running_command(self, snapshot: ProcessSnapshot | None = None) -> bool:
        """True when an agent command is pending or the user has a command running."""
        if self.is_busy():
            return True
        pty = self._pty
        pid = getattr(pty, "pid", None) if pty is not None else None
        if not pid or not self.is_alive():
            return False
        try:
            return shell_has_foreground_child(int(pid), snapshot)
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

    def _keep_output(self, text: str) -> None:
        """Add to the kept history (caller holds _output_cond); the oldest chunks go past the cap."""
        if not text:
            return
        ring = self._output_ring
        if ring and len(ring[-1]) + len(text) <= _OUTPUT_CHUNK_CHARS:
            # A progress code split across two reads is whole again once merged.
            before = len(ring[-1])
            ring[-1] = _PROGRESS_SEQ_RE.sub("", ring[-1] + text)
            self._output_chars += len(ring[-1]) - before
        else:
            ring.append(text)
            self._output_chars += len(text)
        while self._output_chars > _OUTPUT_RING_CHARS and len(ring) > 1:
            self._output_chars -= len(ring.popleft())

    def read_output_tail(self, max_chars: int = 8000) -> str:
        with self._output_cond:
            text = "".join(self._output_ring)
        text = _PROGRESS_SEQ_RE.sub("", _QUERY_SEQ_RE.sub("", text))
        if len(text) > max_chars:
            return text[-max_chars:]
        return text

    def _wrap_agent_command(self, command: str, background: bool) -> str:
        cmd = command.rstrip("\r\n")
        if background:
            if self.shell == "powershell":
                return f"{cmd}\r"
            return f"({cmd}) &\r\n"
        if self.shell == "powershell":
            # "$LASTEXITCODE__" inside a string is a variable named LASTEXITCODE__ (an
            # underscore is a name character), so the marker printed no code and every
            # PowerShell command waited until it timed out. A cmdlet sets no
            # $LASTEXITCODE at all: report $? then, and clear a stale code first.
            # Enter in a PowerShell console is a bare carriage return; "\r\n" left a
            # ">>" continuation prompt behind.
            return (
                'Write-Output ("__DUCKY_{0}__" -f "BEGIN"); '
                f"$global:LASTEXITCODE = 0; {cmd}; $__duckyOk = $?; "
                'Write-Output ("__DUCKY_DONE__{0}__" -f $(if ($LASTEXITCODE) { $LASTEXITCODE } '
                "elseif ($__duckyOk) { 0 } else { 1 }))\r"
            )
        return f"printf '__DUCKY_%s__\\n' BEGIN; {cmd}; echo __DUCKY_DONE__$?__\r\n"

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
                "output_tail": agent_command_output(self.read_output_tail(_OUTPUT_RING_CHARS)),
            }
        self.set_busy(False)
        return {
            "ok": True,
            "exit_code": self._pending_exit_code,
            "output_tail": agent_command_output(self.read_output_tail(_OUTPUT_RING_CHARS)),
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
            "hidden": self.hidden,
        }
