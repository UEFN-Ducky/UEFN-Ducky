"""Opening Settings → Feedback froze the app (Oct 26): the tab read and scrubbed every
log row to show two counts, and the click's fullscreen topmost confetti overlay only
went away if every animation frame succeeded."""

from __future__ import annotations

import json
import time

import pytest

from frontend import error_log


def test_counts_never_read_the_rows(tmp_path, monkeypatch):
    monkeypatch.setattr(error_log, "_use_db", lambda: False)
    monkeypatch.setattr(error_log, "_path", lambda name: tmp_path / name)
    now = time.time()
    (tmp_path / "errors.jsonl").write_text(
        "".join(json.dumps({"ts": now, "source": "t", "message": f"e{i}"}) + "\n" for i in range(3)), encoding="utf-8"
    )
    (tmp_path / "activity.jsonl").write_text(
        json.dumps({"ts": now - 2 * error_log.MAX_AGE_S, "source": "t", "message": "old"}) + "\n"
        + json.dumps({"ts": now, "source": "t", "message": "new"}) + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(error_log, "_scrub", lambda _t: pytest.fail("counting must not scrub rows"))
    assert error_log.counts() == {"errors": 3, "log": 1}


def test_the_feedback_click_never_opens_a_native_overlay():
    # The click's fullscreen topmost Tk overlay keyed out magenta; where the key failed
    # (Oct 28, every PC) the whole screen went pink and the overlay ate all input.
    from pathlib import Path

    from frontend.ui_web.panel_api import PanelApi

    assert not hasattr(PanelApi, "burst_desktop_confetti")
    src = Path(__file__).parent / "ui_web" / "web" / "src" / "utils" / "confettiBurst.ts"
    assert "burst_desktop_confetti" not in src.read_text(encoding="utf-8")
