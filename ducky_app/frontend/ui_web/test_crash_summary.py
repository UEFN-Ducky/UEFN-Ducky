import threading

from frontend.ui_web.agent_turns import errors as am


def test_crashed_turn_with_writes_names_the_files():
    row = am._crash_row(
        {"role": "assistant", "content": "Fixing the limo."},
        "Ducky AI is busy right now",
        tools_run=20,
        written={"Verse/limo_controller.verse", "Verse/a.verse", "Verse/b.verse"},
        auto_resume=True,
    )
    assert row["incomplete"] and row["error"] == "Ducky AI is busy right now"
    assert row["content"].startswith("Fixing the limo.\n\n**Interrupted** (Ducky AI is busy right now) after 20 tool calls")
    for f in ("limo_controller.verse", "a.verse", "b.verse"):
        assert f in row["content"]
    assert "Resuming in 60 s." in row["content"]


def test_crash_without_tools_keeps_empty_row_and_offers_resume():
    assert am._crash_row(None, "boom", tools_run=0, written=set(), auto_resume=False)["content"] == ""
    row = am._crash_row(None, "bad key", tools_run=1, written=set(), auto_resume=False)
    assert row["content"].endswith("no files written. Press Resume to continue.")


def test_transient_crash_auto_resumes_once(monkeypatch):
    started = []

    class T:
        def __init__(self, delay, fn):
            started.append(delay)
            self.daemon = False

        def start(self):
            pass

    monkeypatch.setattr(threading, "Timer", T)
    monkeypatch.setattr(am, "_auto_resumed_at", {})
    assert am._is_transient("Ducky AI is busy right now") and not am._is_transient("bad key")
    assert am._schedule_auto_resume("c1", "busy", "agent") is True
    assert am._schedule_auto_resume("c1", "busy", "agent") is False
    assert started == [am.AUTO_RESUME_DELAY_S]
