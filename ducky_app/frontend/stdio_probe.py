"""Run the configured bridge command and perform a minimal MCP stdio handshake."""

from __future__ import annotations

import json

from frontend import __version__
import queue
import subprocess
import threading
from typing import Any


def _encode_line(obj: dict[str, Any]) -> bytes:
    """MCP stdio transport uses newline-delimited JSON (one JSON object per line)."""
    return (json.dumps(obj, separators=(",", ":")) + "\n").encode("utf-8")


def _read_one_line_json(stream) -> dict[str, Any] | None:
    line = stream.readline()
    if not line:
        return None
    try:
        return json.loads(line.decode("utf-8"))
    except json.JSONDecodeError:
        return None


def _stderr_reader(proc: subprocess.Popen, out: list[str]) -> None:
    if not proc.stderr:
        return
    try:
        data = proc.stderr.read().decode("utf-8", errors="replace")
        if data:
            out.append(data)
    except Exception:
        pass


def _stdout_reader(proc: subprocess.Popen, q: queue.Queue) -> None:
    if not proc.stdout:
        return
    try:
        msg = _read_one_line_json(proc.stdout)
        q.put(msg)
    except Exception:
        q.put(None)


def probe_stdio_mcp(command: str, args: list[str], timeout_sec: float = 15.0) -> tuple[bool, str]:
    """
    Spawn command with args, send initialize, expect JSON-RPC result.
    Returns (ok, detail_text).
    """
    cmd_line = f"{command} {' '.join(args)}"
    try:
        proc = subprocess.Popen(
            [command, *args],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            bufsize=0,
        )
    except OSError as e:
        return False, f"spawn failed: {e}\nCommand: {cmd_line}"

    stderr_body: list[str] = []
    t_err = threading.Thread(target=_stderr_reader, args=(proc, stderr_body), daemon=True)
    t_err.start()

    msg_q: queue.Queue = queue.Queue()
    t_out = threading.Thread(target=_stdout_reader, args=(proc, msg_q), daemon=True)

    assert proc.stdin is not None
    init_id = 1
    init_msg = {
        "jsonrpc": "2.0",
        "id": init_id,
        "method": "initialize",
        "params": {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "uefn-ducky-panel", "version": __version__},
        },
    }
    t_out.start()
    try:
        proc.stdin.write(_encode_line(init_msg))
        proc.stdin.flush()
    except BrokenPipeError:
        _kill(proc)
        t_err.join(timeout=2.0)
        return False, f"broken pipe\nstderr:\n{''.join(stderr_body)}"

    try:
        response = msg_q.get(timeout=timeout_sec)
    except queue.Empty:
        _kill(proc)
        t_err.join(timeout=2.0)
        return False, f"timeout waiting for initialize\nstderr:\n{''.join(stderr_body)}"

    if response and response.get("id") == init_id and "result" in response:
        notif = {
            "jsonrpc": "2.0",
            "method": "notifications/initialized",
            "params": {},
        }
        try:
            proc.stdin.write(_encode_line(notif))
            proc.stdin.flush()
        except BrokenPipeError:
            pass
        _kill(proc)
        t_err.join(timeout=2.0)
        return True, "initialize OK"

    if response and "error" in response:
        err = response["error"]
        _kill(proc)
        t_err.join(timeout=2.0)
        return False, f"MCP error: {err}\nstderr:\n{''.join(stderr_body)}"

    code = proc.poll()
    _kill(proc)
    t_err.join(timeout=2.0)
    return False, f"unexpected response (exit={code})\nstderr:\n{''.join(stderr_body)}\nresponse={response!r}"


def _kill(proc: subprocess.Popen) -> None:
    try:
        if proc.poll() is None:
            proc.terminate()
            proc.wait(timeout=3)
    except subprocess.TimeoutExpired:
        try:
            proc.kill()
        except OSError:
            pass
    except OSError:
        pass
    for pipe in (proc.stdin, proc.stdout, proc.stderr):
        if pipe:
            try:
                pipe.close()
            except OSError:
                pass
