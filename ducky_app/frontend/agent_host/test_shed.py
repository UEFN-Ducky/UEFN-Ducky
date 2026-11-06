"""Host side of ``shed``: an idle warm host unloads engines, empties turn caches and trims."""

from __future__ import annotations

import os
import sys
import threading
import time
import types
import uuid

from frontend.agent_host import main as host_main
from frontend.agent_host import protocol, supervisor, tool_server, turns


def _fakes(monkeypatch, *, trim: bool = True) -> list[str]:
    calls: list[str] = []
    monkeypatch.setattr(tool_server, "shed_engines", lambda: calls.append("engines") or ["forge"])
    if trim:
        fake = types.ModuleType("frontend.heap_trim")
        fake.trim = lambda: calls.append("trim")  # type: ignore[attr-defined]
        monkeypatch.setitem(sys.modules, "frontend.heap_trim", fake)
    else:
        monkeypatch.setitem(sys.modules, "frontend.heap_trim", None)  # import fails
    return calls


def test_shed_unloads_engines_drops_caches_and_trims(monkeypatch) -> None:
    from backend.store.repos import chats
    from frontend.ui_web.chats import saves

    calls = _fakes(monkeypatch)
    monkeypatch.setattr(turns, "_cancels", {})
    monkeypatch.setitem(saves._PREFIX_STATS, "c-idle", ([{"text": "x" * 1000}], 0, frozenset()))
    chats._part_keep("acct", "digest-shed", "part text")

    out = host_main.shed()

    assert calls == ["engines", "trim"]
    assert out["engines"] == ["forge"] and out["trimmed"] is True
    assert "c-idle" not in saves._PREFIX_STATS
    assert chats._part_cached("acct", "digest-shed") is None
    assert set(out["caches"]) == {"prefix_stats", "parts_open"}
    assert isinstance(out["collected"], int)


def test_shed_without_heap_trim_still_sheds(monkeypatch) -> None:
    calls = _fakes(monkeypatch, trim=False)
    monkeypatch.setattr(turns, "_cancels", {})
    out = host_main.shed()
    assert calls == ["engines"] and out["trimmed"] is False


def test_shed_skips_while_a_turn_runs(monkeypatch) -> None:
    calls = _fakes(monkeypatch)
    monkeypatch.setattr(turns, "_cancels", {"r1": threading.Event()})
    assert host_main.shed() == {"skipped": "turns running"}
    assert calls == []


def test_a_turn_end_forgets_its_checkpoint_prefix(monkeypatch) -> None:
    from frontend.ui_web.chats import saves

    monkeypatch.setitem(saves._PREFIX_STATS, "c-done", ([{"text": "y"}], 0, frozenset()))
    monkeypatch.setitem(saves._PREFIX_STATS, "c-other", ([{"text": "z"}], 0, frozenset()))
    turns._forget_turn_caches("c-done")
    assert "c-done" not in saves._PREFIX_STATS and "c-other" in saves._PREFIX_STATS


def test_the_shed_frame_reaches_the_host(monkeypatch) -> None:
    got = threading.Event()
    monkeypatch.setattr(host_main, "shed", lambda: got.set())
    tag = f"t-{uuid.uuid4().hex[:8]}"
    key = os.urandom(16)
    listener = protocol.listen(tag, key)
    done = threading.Event()
    host = threading.Thread(target=host_main.serve, args=(protocol.pipe_name(tag), key, done), daemon=True)
    host.start()
    conn = listener.accept()
    listener.close()
    app = protocol.Channel(conn, send_ops=supervisor.APP_OPS, recv_ops=protocol.HOST_OPS,
                           on_frame=lambda f: None).start()
    try:
        deadline = time.monotonic() + 5
        while host_main._batcher is None and time.monotonic() < deadline:
            time.sleep(0.01)
        assert app.send("shed")
        assert got.wait(5)
        assert host.is_alive()  # the host stays up
    finally:
        done.set()
        host.join(5)
        app.close()
