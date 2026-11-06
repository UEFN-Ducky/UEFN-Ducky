"""The panel event backlog stays under its byte and count caps, oldest dropped first, and
the newest event always stays so pollers still receive it."""

from __future__ import annotations

from collections import deque

import pytest

from frontend.ui_web import panel_httpd


@pytest.fixture(autouse=True)
def _empty(monkeypatch):
    monkeypatch.setattr(panel_httpd, "_event_backlog", deque())
    monkeypatch.setattr(panel_httpd, "_event_backlog_bytes", 0)


def _event(i: int, size: int) -> dict[str, object]:
    return {"type": "tool_result", "conv_id": "c1", "i": i, "text": "x" * size}


def test_the_byte_cap_is_4_mb_and_drops_the_oldest_first() -> None:
    assert panel_httpd._EVENT_BACKLOG_MAX_BYTES == 4 * 1024 * 1024
    for i in range(400):  # 400 x 50 KB = 20 MB published
        panel_httpd.publish_panel_events([_event(i, 50_000)])
    rows = panel_httpd._event_backlog
    assert panel_httpd._event_backlog_bytes == sum(r[2] for r in rows) <= 4 * 1024 * 1024
    kept = [r[1]["i"] for r in rows]
    assert kept == list(range(kept[0], 400)) and kept[0] > 300  # newest kept, in order


def test_the_count_cap_holds_for_small_events(monkeypatch) -> None:
    monkeypatch.setattr(panel_httpd, "_EVENT_BACKLOG_MAX", 50)
    panel_httpd.publish_panel_events([_event(i, 10) for i in range(200)])
    assert len(panel_httpd._event_backlog) == 50
    assert panel_httpd._event_backlog[-1][1]["i"] == 199


def test_one_event_bigger_than_the_cap_still_stays() -> None:
    panel_httpd.publish_panel_events([_event(0, 10), _event(1, 5 * 1024 * 1024)])
    assert [r[1]["i"] for r in panel_httpd._event_backlog] == [1]
    seq = panel_httpd._event_backlog[-1][0]
    cursor, events = panel_httpd._poll_panel_events(seq - 1, timeout=0)
    assert cursor == seq and events and events[-1]["i"] == 1
