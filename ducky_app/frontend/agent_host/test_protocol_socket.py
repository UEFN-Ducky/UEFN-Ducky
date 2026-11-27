"""The agent host's channel address: a named pipe on Windows, an AF_UNIX socket on Linux."""

from __future__ import annotations

import os
import socket
import stat
import sys
import tempfile
import threading
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from frontend.agent_host import protocol  # noqa: E402

posix = pytest.mark.skipif(os.name == "nt", reason="AF_UNIX socket path is Linux-only")


@pytest.mark.skipif(os.name != "nt", reason="Windows pipe name")
def test_windows_pipe_name_is_unchanged():
    assert protocol.pipe_name(3, 42) == r"\\.\pipe\uefn-ducky-host-42-3"


@posix
def test_socket_lives_in_runtime_dir_and_stays_short(monkeypatch):
    # a short folder: xdist's tmp_path (/tmp/pytest-of-runner/pytest-0/popen-gw1/...) already hashes
    run = tempfile.mkdtemp(prefix="ud", dir="/tmp")
    monkeypatch.setenv("XDG_RUNTIME_DIR", run)
    try:
        assert protocol.pipe_name(3, 42) == os.path.join(run, "uefn-ducky", "uefn-ducky-host-42-3.sock")
    finally:
        os.rmdir(run)
    monkeypatch.setenv("XDG_RUNTIME_DIR", "/tmp")
    long_slot = "rpc-" + "x" * 200
    name = protocol.pipe_name(long_slot, 42)
    assert len(os.fsencode(name)) <= protocol._MAX_SUN_PATH
    assert name != protocol.pipe_name("rpc-" + "y" * 200, 42)


@posix
def test_a_long_folder_like_the_macos_tmpdir_falls_back_to_tmp(monkeypatch):
    run = tempfile.mkdtemp(prefix="ud" + "d" * 120)
    monkeypatch.setenv("XDG_RUNTIME_DIR", run)
    try:
        name = protocol.pipe_name("modes", 42)
    finally:
        os.rmdir(run)
    assert os.path.dirname(name) == f"/tmp/ducky-{os.getuid()}" and len(os.fsencode(name)) <= protocol._MAX_SUN_PATH


@posix
def test_a_socket_folder_another_user_made_first_is_refused(tmp_path, monkeypatch):
    folder = tmp_path / "theirs"
    folder.mkdir()
    owner = os.stat(folder).st_uid
    monkeypatch.setattr(os, "getuid", lambda: owner + 1)
    with pytest.raises(PermissionError):
        protocol.listen_at(str(folder / "x.sock"), b"k" * 32)


@posix
def test_socket_is_0600_in_a_0700_folder_and_removed_on_close(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    name = protocol.pipe_name("modes", 42)
    listener = protocol.listen("modes", b"k" * 32, app_pid=42)
    try:
        assert stat.S_IMODE(os.stat(name).st_mode) == 0o600
        assert stat.S_IMODE(os.stat(os.path.dirname(name)).st_mode) == 0o700
    finally:
        listener.close()
    assert not os.path.exists(name)


@posix
def test_stale_socket_from_a_dead_app_is_replaced(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    name = protocol.pipe_name("stale", 42)
    os.makedirs(os.path.dirname(name), mode=0o700, exist_ok=True)  # macOS: the shared /tmp/ducky-<uid>
    dead = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    dead.bind(name)
    dead.close()  # the file stays, nobody listens: what a crashed app leaves
    assert os.path.exists(name)
    listener = protocol.listen("stale", b"k" * 32, app_pid=42)
    try:
        frames = []
        app = None
        host = threading.Thread(target=lambda: frames.append(
            protocol.connect_app(name, b"k" * 32, lambda f: None, heartbeat_s=0)), daemon=True)
        host.start()
        app = protocol.accept_host(listener, lambda f: None, dead_after_s=0)
        host.join(5)
        assert frames and not app.closed
    finally:
        listener.close()
        if app:
            app.close()
        for ch in frames:
            ch.close()


@posix
def test_fallback_without_runtime_dir_uses_app_cache(monkeypatch):
    # a short folder: under xdist's tmp_path the socket path passes 107 bytes, and /tmp/ducky-<uid> rightly wins
    cache = tempfile.mkdtemp(prefix="uc", dir="/tmp")
    monkeypatch.delenv("XDG_RUNTIME_DIR", raising=False)
    monkeypatch.setenv("XDG_CACHE_HOME", cache)
    try:
        if sys.platform.startswith("linux"):
            assert protocol.pipe_name(1, 2).startswith(os.path.join(cache, "UEFN-Ducky", "sock"))
    finally:
        os.rmdir(cache)
