"""The panel event backlog stays under its byte and count caps, oldest dropped first, and
the newest event always stays so pollers still receive it."""

from __future__ import annotations

import pytest

from frontend.ui_web import event_backlog, panel_httpd
from frontend.ui_web.event_backlog import EventBacklog


@pytest.fixture(autouse=True)
def _empty(monkeypatch):
    monkeypatch.setattr(panel_httpd, "_event_backlog", EventBacklog())


def _event(i: int, size: int) -> dict[str, object]:
    return {"type": "tool_result", "conv_id": "c1", "i": i, "text": "x" * size}


def test_the_byte_cap_is_4_mb_and_drops_the_oldest_first() -> None:
    assert event_backlog.MAX_BYTES == 4 * 1024 * 1024
    for i in range(400):  # 400 x 50 KB = 20 MB published
        panel_httpd.publish_panel_events([_event(i, 50_000)])
    backlog = panel_httpd._event_backlog
    assert backlog.bytes == sum(r[2] for r in backlog.rows) <= 4 * 1024 * 1024
    kept = [r[1]["i"] for r in backlog.rows]
    assert kept == list(range(kept[0], 400)) and kept[0] > 300  # newest kept, in order


def test_the_count_cap_holds_for_small_events(monkeypatch) -> None:
    monkeypatch.setattr(panel_httpd, "_event_backlog", EventBacklog(max_events=50))
    panel_httpd.publish_panel_events([_event(i, 10) for i in range(200)])
    rows = panel_httpd._event_backlog.rows
    assert len(rows) == 50 and rows[-1][1]["i"] == 199


def test_one_event_bigger_than_the_cap_still_stays() -> None:
    panel_httpd.publish_panel_events([_event(0, 10), _event(1, 5 * 1024 * 1024)])
    rows = panel_httpd._event_backlog.rows
    assert [r[1]["i"] for r in rows] == [1]
    seq = rows[-1][0]
    cursor, events = panel_httpd._poll_panel_events(seq - 1, timeout=0)
    assert cursor == seq and events and events[-1]["i"] == 1
