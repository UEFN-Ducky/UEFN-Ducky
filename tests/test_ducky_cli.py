"""ducky.ps1 talks to one loopback panel, then exits.

A one-shot must not keep polling, must not tight-loop, and must not start
the app when the panel port is already open.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import threading
import time
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

import pytest

ROOT = Path(__file__).resolve().parents[1]
PS1 = ROOT / "release" / "installer" / "ducky.ps1"

_CONV = {
    "id": "c1",
    "title": "Door",
    "updated": 2,
    "ducky_name": "Ada",
    "profile_id": "p1",
    "model": "anthropic:test",
}


class _Panel(BaseHTTPRequestHandler):
    def _json(self, obj: object) -> None:
        data = json.dumps(obj).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length)
        path = urlparse(self.path).path
        self.server.posts.append(path)  # type: ignore[attr-defined]
        self.server.bodies.append(body)  # type: ignore[attr-defined]
        if path.endswith("/send_message"):
            result: object = {"run_id": "r1"}
        elif path.endswith("/list_all_conversations"):
            result = [dict(_CONV)]
        elif path.endswith("/get_project_info"):
            result = {"path": "C:/island", "name": "island"}
        else:
            result = {}
        self._json({"ok": True, "result": result})

    def do_GET(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        if path != "/__panel_events":
            self.send_error(404)
            return
        self.server.gets += 1  # type: ignore[attr-defined]
        mode = self.server.mode  # type: ignore[attr-defined]
        if mode == "done":
            events = [
                {"type": "text_delta", "text": "hello", "conv_id": "c1"},
                {"type": "assistant_done", "conv_id": "c1"},
            ]
        elif mode == "error":
            events = [{"type": "error", "text": "no key", "conv_id": "c1"}]
        else:
            events = []
        self._json({"cursor": self.server.gets, "events": events})  # type: ignore[attr-defined]

    def log_message(self, fmt: str, *args: object) -> None:
        return


def _server(mode: str) -> ThreadingHTTPServer:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Panel)
    server.posts = []  # type: ignore[attr-defined]
    server.bodies = []  # type: ignore[attr-defined]
    server.gets = 0  # type: ignore[attr-defined]
    server.mode = mode  # type: ignore[attr-defined]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


@pytest.fixture
def closed_panel_port() -> Iterator[int]:
    # Hold a bound, non-listening socket for the whole test. Picking a free port
    # and closing it would race another listener or a client's ephemeral source
    # port: TCP can connect to itself when source and destination are identical.
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as reserved:
        if os.name == "nt":
            reserved.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        reserved.bind(("127.0.0.1", 0))
        yield reserved.getsockname()[1]


def _env(panel_port: int, tmp_path: Path, **extra: str) -> tuple[dict[str, str], Path]:
    marker = tmp_path / "launched.txt"
    fake_exe = tmp_path / "not-the-app.cmd"
    # Append, so a start-loop would show up as more than one line.
    fake_exe.write_text(f'@echo off\r\necho launched>>"{marker}"\r\n', encoding="utf-8")
    env = os.environ.copy()
    env["DUCKY_EXE"] = str(fake_exe)
    env["DUCKY_SESSION_FILE"] = str(tmp_path / "cli-session.json")
    env["DUCKY_PANEL_PORT"] = str(panel_port)
    env.update(extra)
    return env, marker


def _run(args: list[str], env: dict[str, str], timeout: float, stdin: str | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["powershell.exe", "-NoLogo", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(PS1), *args],
        capture_output=True,
        text=True,
        timeout=timeout,
        env=env,
        input=stdin,
        check=False,
    )


def _quiet_after(server: ThreadingHTTPServer) -> None:
    """Polling must stop once the client process has exited."""
    seen = server.gets  # type: ignore[attr-defined]
    time.sleep(0.4)
    assert server.gets == seen  # type: ignore[attr-defined]


def test_cli_streams_reply_without_launching_the_app(tmp_path: Path) -> None:
    server = _server("done")
    env, marker = _env(server.server_address[1], tmp_path)
    try:
        proc = _run(["hello", "from", "cli"], env, timeout=30)
        assert proc.returncode == 0, proc.stderr
        assert "hello" in proc.stdout
        assert not marker.exists()
        assert server.gets == 1  # type: ignore[attr-defined]
        sent = [
            json.loads(body.decode("utf-8"))
            for path, body in zip(server.posts, server.bodies, strict=True)  # type: ignore[attr-defined]
            if path.endswith("/send_message")
        ]
        assert len(sent) == 1
        args = sent[0]["args"]
        assert args["conv_id"] == "c1"
        assert args["text"] == "hello from cli"
        assert args["mode"] == "agent"
        assert args["model"] == "anthropic:test"
        _quiet_after(server)
    finally:
        server.shutdown()


def test_reply_error_exits_and_stops_polling(tmp_path: Path) -> None:
    server = _server("error")
    env, marker = _env(server.server_address[1], tmp_path)
    try:
        proc = _run(["hi"], env, timeout=20)
        assert proc.returncode == 1
        assert "no key" in proc.stderr
        assert not marker.exists()
        assert server.gets == 1  # type: ignore[attr-defined]
        _quiet_after(server)
    finally:
        server.shutdown()


def test_missing_reply_exits_without_a_tight_loop(tmp_path: Path) -> None:
    server = _server("empty")
    env, marker = _env(server.server_address[1], tmp_path, DUCKY_REPLY_TIMEOUT_SEC="1")
    try:
        started = time.monotonic()
        proc = _run(["hi"], env, timeout=15)
        elapsed = time.monotonic() - started
        assert proc.returncode == 1
        assert "Timed out" in proc.stderr
        assert not marker.exists()
        # 50ms pause between empty polls: 1s is tens of requests, not a spin.
        assert 1 <= server.gets <= 40  # type: ignore[attr-defined]
        assert elapsed < 8
        _quiet_after(server)
    finally:
        server.shutdown()


def test_closed_port_starts_the_app_once_then_exits(tmp_path: Path, closed_panel_port: int) -> None:
    env, marker = _env(closed_panel_port, tmp_path, DUCKY_START_TIMEOUT_SEC="1")
    started = time.monotonic()
    proc = _run(["hi"], env, timeout=15)
    elapsed = time.monotonic() - started
    assert proc.returncode == 1
    assert "did not open" in proc.stderr
    assert marker.read_text(encoding="utf-8").strip().count("launched") == 1
    assert elapsed < 8


def test_status_and_mode_do_not_start_the_app(tmp_path: Path, closed_panel_port: int) -> None:
    env, marker = _env(closed_panel_port, tmp_path)
    status = _run(["status"], env, timeout=15)
    assert status.returncode == 0, status.stderr
    assert "not running" in status.stdout
    mode = _run(["mode", "plan"], env, timeout=15)
    assert mode.returncode == 0, mode.stderr
    assert mode.stdout.strip() == "plan"
    assert not marker.exists()


def test_repl_exit_does_not_poll(tmp_path: Path) -> None:
    server = _server("done")
    env, marker = _env(server.server_address[1], tmp_path)
    try:
        proc = _run([], env, timeout=20, stdin="exit\n")
        assert proc.returncode == 0, proc.stderr
        assert server.gets == 0  # type: ignore[attr-defined]
        assert not any(path.endswith("/send_message") for path in server.posts)  # type: ignore[attr-defined]
        assert not marker.exists()
        _quiet_after(server)
    finally:
        server.shutdown()
