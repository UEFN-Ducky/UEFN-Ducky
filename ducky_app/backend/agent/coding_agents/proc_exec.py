"""Hidden-window subprocess executor for CLI coding agents.

Replaces the PTY/PowerShell scrape path for non-interactive runs: env is passed
as a real environment dict (no ``$env:`` one-liner quoting), stdout is consumed
line-by-line for JSON event streams, and the process handle is registered per
conversation so cancel actually kills the CLI.
"""

from __future__ import annotations

import os
import subprocess
import threading
import time
from dataclasses import dataclass, field
from typing import Callable

_procs: dict[str, subprocess.Popen] = {}
_procs_lock = threading.Lock()

_STDERR_TAIL_CHARS = 8000


def register_process(conv_id: str, proc: subprocess.Popen) -> None:
    with _procs_lock:
        prev = _procs.get(conv_id)
        if prev is not None and prev is not proc and prev.poll() is None:
            try:
                prev.kill()
            except OSError:
                pass
        _procs[conv_id] = proc


def unregister_process(conv_id: str, proc: subprocess.Popen) -> None:
    with _procs_lock:
        if _procs.get(conv_id) is proc:
            _procs.pop(conv_id, None)


def terminate_conv_process(conv_id: str) -> bool:
    """Kill the running coding-agent process for a chat (cancel path)."""
    with _procs_lock:
        proc = _procs.get(conv_id)
    if proc is None or proc.poll() is not None:
        return False
    try:
        proc.kill()
    except OSError:
        return False
    return True


@dataclass
class ProcResult:
    returncode: int
    timed_out: bool = False
    cancelled: bool = False
    stderr_tail: str = ""
    stdout_lines: int = 0
    raw_tail: str = ""
    """Last unparsed stdout text, for error surfaces when no JSON arrived."""
    _raw_ring: list[str] = field(default_factory=list, repr=False)


def run_streaming_process(
    *,
    argv: list[str],
    cwd: str,
    env_extra: dict[str, str],
    conv_id: str,
    on_line: Callable[[str], None],
    timeout_s: float,
    cancel: threading.Event | None = None,
    stdin_data: str | None = None,
) -> ProcResult:
    """Run argv, feeding each stdout line to ``on_line``. Kills on timeout/cancel.

    Pass long user prompts via ``stdin_data`` — never as argv — or Windows
    CreateProcess raises WinError 206 on large pastes.
    """
    env = dict(os.environ)
    for key, value in (env_extra or {}).items():
        if key:
            env[str(key)] = str(value)

    workdir = (cwd or "").strip() or os.getcwd()
    if not os.path.isdir(workdir):
        workdir = os.getcwd()

    use_stdin = stdin_data is not None
    kwargs: dict = {
        "cwd": workdir,
        "env": env,
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
        "stdin": subprocess.PIPE if use_stdin else subprocess.DEVNULL,
        "text": True,
        "encoding": "utf-8",
        "errors": "replace",
        "bufsize": 1,
    }
    if os.name == "nt":
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)

    try:
        proc = subprocess.Popen(argv, **kwargs)
    except OSError as exc:
        # WinError 206 / errno-equivalent: command line or env still too long.
        win = getattr(exc, "winerror", None)
        if win == 206 or "too long" in str(exc).lower():
            raise OSError(
                206,
                "Prompt/command too long for Windows to launch the coding agent. "
                "Ducky should pipe the prompt via stdin/file — restart UEFN-Ducky "
                "and update the Anthropic (Claude Code) Store plugin if this persists.",
            ) from exc
        raise
    register_process(conv_id, proc)
    result = ProcResult(returncode=-1)

    if use_stdin and proc.stdin is not None:
        try:
            proc.stdin.write(stdin_data or "")
            proc.stdin.close()
        except (OSError, ValueError, BrokenPipeError):
            pass

    stderr_chunks: list[str] = []

    def _drain_stderr() -> None:
        try:
            for line in proc.stderr or []:
                stderr_chunks.append(line)
                if sum(len(c) for c in stderr_chunks) > _STDERR_TAIL_CHARS * 2:
                    del stderr_chunks[: len(stderr_chunks) // 2]
        except (OSError, ValueError):
            pass

    def _drain_stdout() -> None:
        try:
            for line in proc.stdout or []:
                stripped = line.rstrip("\r\n")
                if not stripped:
                    continue
                result.stdout_lines += 1
                result._raw_ring.append(stripped)
                if len(result._raw_ring) > 40:
                    del result._raw_ring[:20]
                try:
                    on_line(stripped)
                except Exception:
                    # A presenter bug must not kill the read loop mid-turn.
                    pass
        except (OSError, ValueError):
            pass

    t_err = threading.Thread(target=_drain_stderr, daemon=True, name=f"ca-stderr-{conv_id[:8]}")
    t_out = threading.Thread(target=_drain_stdout, daemon=True, name=f"ca-stdout-{conv_id[:8]}")
    t_err.start()
    t_out.start()

    # timeout_s <= 0 means no wall-clock limit — keep going until the CLI exits
    # or the user cancels. Long UEFN builds (city blockouts, etc.) routinely need
    # more than 15 minutes; killing them mid-turn is worse than waiting.
    limit = float(timeout_s)
    deadline = (time.time() + limit) if limit > 0 else None
    try:
        while proc.poll() is None:
            if cancel is not None and cancel.is_set():
                result.cancelled = True
                proc.kill()
                break
            if deadline is not None and time.time() > deadline:
                result.timed_out = True
                proc.kill()
                break
            time.sleep(0.1)
        proc.wait(timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        pass
    finally:
        t_out.join(timeout=5)
        t_err.join(timeout=2)
        unregister_process(conv_id, proc)

    result.returncode = proc.returncode if proc.returncode is not None else -1
    result.stderr_tail = "".join(stderr_chunks)[-_STDERR_TAIL_CHARS:]
    result.raw_tail = "\n".join(result._raw_ring)[-4000:]
    return result
