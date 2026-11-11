"""A window that fell behind the event backlog is told to reload what it shows."""

from __future__ import annotations

from frontend.ui_web.event_backlog import EventBacklog


def test_a_window_that_missed_dropped_events_is_told_to_resync() -> None:
    backlog = EventBacklog(max_events=3)
    for seq in range(1, 4):
        backlog.add(seq, {"type": "text_delta", "n": seq})
    assert not backlog.missed(0) and not backlog.missed(1)  # nothing dropped yet
    for seq in range(4, 7):
        backlog.add(seq, {"type": "text_delta", "n": seq})
    assert backlog.dropped_through == 3 and [s for s, _ in backlog.since(0)] == [4, 5, 6]
    late = [event for _, event in backlog.since(1)]
    assert backlog.with_resync(1, late) == [{"type": "remote_resync", "reason": "events_missed"}, *late]
    # A window that kept up, and one that has read nothing yet (it loads its own state), are not.
    assert not backlog.missed(3) and backlog.with_resync(3, late) == late
    assert not backlog.missed(0) and backlog.with_resync(0, late) == late


def test_big_events_dropped_by_size_count_as_missed() -> None:
    backlog = EventBacklog(max_events=100, max_bytes=2000)
    for seq in range(1, 6):
        backlog.add(seq, {"type": "tool_done", "text": "x" * 900})
    assert backlog.dropped_through >= 3 and backlog.missed(1)
    assert [s for s, _ in backlog.since(0)][-1] == 5  # the newest always stays


def test_the_poll_leads_with_a_resync_for_a_late_window(monkeypatch) -> None:
    from frontend.ui_web import panel_httpd

    backlog = EventBacklog(max_events=2)
    monkeypatch.setattr(panel_httpd, "_event_backlog", backlog)
    monkeypatch.setattr(panel_httpd, "_event_seq", 0)
    panel_httpd.publish_panel_events([{"type": "text_delta", "conv_id": "c", "text": str(n)} for n in range(5)])
    cursor, events = panel_httpd._poll_panel_events(1, timeout=0.0)
    assert cursor == 5 and events[0] == {"type": "remote_resync", "reason": "events_missed"}
    assert [e["text"] for e in events[1:]] == ["3", "4"]
    cursor, events = panel_httpd._poll_panel_events(cursor, timeout=0.0)
    assert events == []
