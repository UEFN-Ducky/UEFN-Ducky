"""Question deadlines and transport failures must never synthesize an answer."""
import threading


def test_question_gate_uses_real_http_without_a_frontend_push(monkeypatch, tmp_path):
    from backend.panel import rpc
    from frontend.ui_web import panel_httpd

    monkeypatch.setattr(panel_httpd, "_server", None)
    monkeypatch.setattr(panel_httpd, "_root", None)
    monkeypatch.setattr(panel_httpd, "PANEL_UI_HTTP_PORT", 0)
    monkeypatch.setattr(panel_httpd, "_RPC_HANDLER_WAIT_S", 0.01)
    monkeypatch.setattr(panel_httpd, "verify_panel_dist", lambda _root: None)
    panel_httpd.start_panel_ui_server(tmp_path)
    server = panel_httpd._server
    monkeypatch.setattr(rpc, "_RPC_URL", f"http://127.0.0.1:{server.server_address[1]}/__panel_rpc")
    rid, _ = ui_rpc.submit("ask_user", {"conv_id": "http-gate", "questions": [{"id": "q"}]})
    try:
        assert rpc.panel_rpc("question_gate", {"conv_id": "http-gate"}) == {"blocked": True}
        assert not ui_rpc.respond(rid, {"error": "timeout"})
        assert rpc.panel_rpc("question_gate", {"conv_id": "http-gate"}) == {"blocked": True}
        assert ui_rpc.respond(rid, {"ok": True, "answers": {"q": {"text": "yes"}}})
        assert rpc.panel_rpc("question_gate", {"conv_id": "http-gate"}) == {"blocked": False}
    finally:
        ui_rpc.cancel(rid)
        server.shutdown()
        server.server_close()

from frontend.ui_web import ui_rpc


def test_questions_survive_restart_until_answered(monkeypatch):
    rid, _ = ui_rpc.submit("ask_user", {"conv_id": "restart", "questions": [{"id": "q"}]})

    def restart():
        with ui_rpc._lock:
            ui_rpc._pending.clear()
        monkeypatch.setattr(ui_rpc, "_restored_store", "")

    restart()
    assert ui_rpc.question_gate("restart", 0) is True
    assert [event["request_id"] for event in ui_rpc.pending_questions()] == [rid]
    assert not ui_rpc.respond(rid, {"error": "window closed"})
    restart()
    assert ui_rpc.question_gate("restart", 0) is True
    assert ui_rpc.respond(rid, {"ok": True, "answers": {"q": {"text": "continue"}}})
    restart()
    assert ui_rpc.question_gate("restart", 0) is False
    assert ui_rpc.pending_questions() == []


def test_failed_answer_save_keeps_question_pending(monkeypatch):
    from backend.store.repos import kv
    import pytest

    rid, _ = ui_rpc.submit("ask_user", {"conv_id": "save-failed", "questions": [{"id": "q"}]})
    def failed_save(*args):
        raise RuntimeError("disk unavailable")
    monkeypatch.setattr(kv, "meta_set", failed_save)
    with pytest.raises(RuntimeError, match="disk unavailable"):
        ui_rpc.respond(rid, {"ok": True, "answers": {"q": {"text": "continue"}}})
    assert ui_rpc.question_gate("save-failed", 0) is True
    assert ui_rpc.wait(rid, 0) is None


def test_question_never_expires_and_empty_or_error_response_does_not_release(monkeypatch):
    rid, _ = ui_rpc.submit("ask_user", {"conv_id": "waiting", "questions": [{"id": "choice", "required": True}]})
    try:
        monkeypatch.setattr(ui_rpc.time, "monotonic", lambda: 10**12)
        ui_rpc.submit("navigate", {})
        assert ui_rpc.wait(rid, 0) is None
        assert not ui_rpc.respond(rid, {"error": "timeout"})
        assert not ui_rpc.respond(rid, {"ok": True, "answers": {}})
        assert not ui_rpc.respond(rid, {"ok": True, "answers": {"choice": {"skipped": True}}})
        ui_rpc.cancel(rid)
        assert ui_rpc.wait(rid, 0) is None
        assert any(event["request_id"] == rid for event in ui_rpc.pending_questions())
        assert ui_rpc.has_pending_for_conv("waiting")
        assert ui_rpc.question_gate("waiting", 0) is True
        assert ui_rpc.question_gate("other-chat", 0) is False
        assert ui_rpc.respond(rid, {"ok": True, "answers": {"choice": {"selected": ["yes"]}}})
        assert not ui_rpc.has_pending_for_conv("waiting")
        assert ui_rpc.question_gate("waiting", 0) is False
    finally:
        ui_rpc.cancel(rid)


def test_continuation_waits_for_a_real_answer_and_stop_interrupts_wait():
    rid, _ = ui_rpc.submit("ask_user", {"conv_id": "gated", "questions": [{"id": "choice"}]})
    started, finished = threading.Event(), threading.Event()
    def continuation():
        started.set()
        ui_rpc.wait_for_answers("gated")
        finished.set()
    worker = threading.Thread(target=continuation, daemon=True)
    worker.start()
    try:
        assert started.wait(1)
        assert not finished.wait(0.05)
        stop = threading.Event()
        stop.set()
        try:
            ui_rpc.wait_for_answers("gated", stop)
            assert False, "Stop must interrupt rather than approve"
        except InterruptedError:
            pass
        assert ui_rpc.respond(rid, {"ok": True, "answers": {"choice": {"text": "proceed"}}})
        assert finished.wait(1)
    finally:
        ui_rpc.cancel(rid)
        worker.join(1)
