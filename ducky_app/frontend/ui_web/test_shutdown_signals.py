"""Linux: a signal or logind's shutdown runs the app's normal exit, so the db closes cleanly."""

from __future__ import annotations

import signal
import sqlite3
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from frontend.ui_web import shutdown

_APP_ROOT = Path(__file__).resolve().parents[2]

_CHILD = textwrap.dedent(
    """
    import os, sqlite3, sys, time
    sys.path.insert(0, sys.argv[1])
    from frontend.ui_web import shutdown
    db = sqlite3.connect(sys.argv[2])
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("CREATE TABLE drafts (text TEXT)")
    db.execute("INSERT INTO drafts VALUES ('unsaved draft')")  # not committed yet

    def on_exit():
        db.commit()
        db.close()
        os._exit(0)

    assert shutdown.install_exit_signals(on_exit)
    print("ready", flush=True)
    while True:
        time.sleep(0.05)
    """
)


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Linux signals")
@pytest.mark.parametrize("name", shutdown.EXIT_SIGNALS)
def test_a_signal_runs_the_exit_and_closes_the_db(tmp_path: Path, name: str) -> None:
    sig = getattr(signal, name)
    db_path = tmp_path / "ducky.db"
    child = subprocess.Popen([sys.executable, "-c", _CHILD, str(_APP_ROOT), str(db_path)],
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        assert child.stdout.readline().strip() == "ready", child.stderr.read()
        child.send_signal(sig)
        assert child.wait(10) == 0, child.stderr.read()
    finally:
        child.kill()
    assert not Path(str(db_path) + "-wal").exists()  # last connection closed: WAL checkpointed away
    rows = sqlite3.connect(db_path).execute("SELECT text FROM drafts").fetchall()
    assert rows == [("unsaved draft",)]


def test_logind_prepare_for_shutdown_runs_the_exit() -> None:
    lines = ("/org/freedesktop/login1: org.freedesktop.login1.Manager.PrepareForShutdown (false,)\n"
             "/org/freedesktop/login1: org.freedesktop.login1.Manager.PrepareForShutdown (true,)\n")
    fake = [sys.executable, "-c", f"import sys; sys.stdout.write({lines!r})"]
    calls: list[int] = []
    shutdown.watch_logind(lambda: calls.append(1), cmd=fake)
    assert calls == [1]


def test_a_cancelled_shutdown_does_not_exit() -> None:
    fake = [sys.executable, "-c", "print('org.freedesktop.login1.Manager.PrepareForShutdown (false,)')"]
    calls: list[int] = []
    shutdown.watch_logind(lambda: calls.append(1), cmd=fake)
    assert calls == []


def test_windows_keeps_its_own_exit_path(monkeypatch) -> None:
    monkeypatch.setattr(sys, "platform", "win32")
    assert shutdown.install_exit_signals(lambda: None) is False
