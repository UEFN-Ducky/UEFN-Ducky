from __future__ import annotations

import threading

import frontend.remote_direct as rd


def _offer() -> dict:
    return {"type": "offer", "sdp": "v=0\r\na=fingerprint:sha-256 AB:CD\r\n"}


def test_rtc_connect_rejects_bad_input(monkeypatch) -> None:
    monkeypatch.setattr(rd, "ANSWER_WAIT_S", 0.05)
    assert rd.rtc_connect({})["error"] == "invalid session"
    assert rd.rtc_connect({"session": "abc", "offer": {"type": "answer", "sdp": "x"}, "protocol": 1})["error"] == "invalid offer"
    out = rd.rtc_connect({"session": "abc", "offer": _offer(), "protocol": 99})
    assert out["error"] == "protocol mismatch" and out["protocol"] == rd.PROTOCOL_VERSION


def test_rtc_connect_hands_offer_to_page_and_returns_answer(monkeypatch) -> None:
    published: list[dict] = []
    monkeypatch.setattr("frontend.ui_web.panel_httpd.publish_panel_events", lambda rows: published.extend(rows))
    monkeypatch.setattr(rd, "ANSWER_WAIT_S", 2.0)

    def page():
        for _ in range(100):
            if published:
                break
            threading.Event().wait(0.01)
        ev = published[0]
        assert ev["type"] == "direct_rtc" and ev["session"] == "s1" and ev["offer"]["type"] == "offer"
        assert ev["ice"] == [{"urls": "stun:x"}]
        assert rd.resolve_answer("s1", {"type": "answer", "sdp": "v=0"}, fingerprint="sha-256 AA")

    t = threading.Thread(target=page)
    t.start()
    out = rd.rtc_connect({"session": "s1", "offer": _offer(), "protocol": 1, "ice": [{"urls": "stun:x"}, {"bad": 1}]})
    t.join()
    assert out["answer"] == {"type": "answer", "sdp": "v=0"}
    assert out["fingerprint"] == "sha-256 AA"
    assert out["protocol"] == rd.PROTOCOL_VERSION


def test_rtc_connect_times_out_and_reports(monkeypatch) -> None:
    monkeypatch.setattr("frontend.ui_web.panel_httpd.publish_panel_events", lambda rows: None)
    monkeypatch.setattr(rd, "ANSWER_WAIT_S", 0.05)
    out = rd.rtc_connect({"session": "s2", "offer": _offer(), "protocol": 1})
    assert "did not answer" in out["error"]
    assert rd.pending_sessions() == []


def test_retry_with_the_same_session_joins_the_wait_and_gets_one_offer(monkeypatch) -> None:
    """The mailbox reply was lost and the viewer asks again: one offer to the page, one answer for both."""
    published: list[dict] = []
    monkeypatch.setattr("frontend.ui_web.panel_httpd.publish_panel_events", lambda rows: published.extend(rows))
    monkeypatch.setattr(rd, "ANSWER_WAIT_S", 2.0)
    outs: list[dict] = []
    args = {"session": "s3", "offer": _offer(), "protocol": 1}
    first = threading.Thread(target=lambda: outs.append(rd.rtc_connect(args)))
    first.start()
    while not published:
        threading.Event().wait(0.005)
    second = threading.Thread(target=lambda: outs.append(rd.rtc_connect(args)))
    second.start()
    threading.Event().wait(0.05)
    assert rd.resolve_answer("s3", {"type": "answer", "sdp": "v=0"})
    first.join()
    second.join()
    assert len(published) == 1
    assert [o.get("answer") for o in outs] == [{"type": "answer", "sdp": "v=0"}] * 2
    # A third ask after the answer (reconnect resumes the session) replays it at once.
    assert rd.rtc_connect(args)["answer"] == {"type": "answer", "sdp": "v=0"}
    assert len(published) == 1


def test_a_late_answer_is_kept_for_the_retry(monkeypatch) -> None:
    published: list[dict] = []
    monkeypatch.setattr("frontend.ui_web.panel_httpd.publish_panel_events", lambda rows: published.extend(rows))
    monkeypatch.setattr(rd, "ANSWER_WAIT_S", 0.05)
    args = {"session": "s4", "offer": _offer(), "protocol": 1}
    assert "did not answer" in rd.rtc_connect(args)["error"]
    assert rd.resolve_answer("s4", {"type": "answer", "sdp": "v=1"})
    assert rd.rtc_connect(args)["answer"] == {"type": "answer", "sdp": "v=1"}
    assert len(published) == 1


def test_resolve_answer_rejects_unknown_or_bad() -> None:
    assert rd.resolve_answer("nope", {"type": "answer", "sdp": "v=0"}) is False


def test_note_report_keeps_only_known_fields() -> None:
    rd.note_report({"state": "live", "reason": "", "connect_ms": 812, "candidate": "srflx", "junk": 1})
    rep = rd.last_report()
    assert rep["state"] == "live" and rep["connect_ms"] == 812 and "junk" not in rep and "at" in rep


def test_note_report_forwards_health_without_wiping_link_state(monkeypatch) -> None:
    from frontend import usage_stats

    sent: list[dict] = []
    monkeypatch.setattr(usage_stats, "remote_link", sent.append)
    rd.note_report({"state": "live", "candidate": "host"})
    probe = {"input": {"probes": 6, "lost": 1, "p50_ms": 40, "max_ms": 90}, "client": "android_app", "net": "wifi", "junk": 1}
    rd.note_report(probe)
    assert sent[-1] == {k: v for k, v in probe.items() if k != "junk"}
    rep = rd.last_report()
    assert rep["state"] == "live" and rep["candidate"] == "host" and "input" not in rep
