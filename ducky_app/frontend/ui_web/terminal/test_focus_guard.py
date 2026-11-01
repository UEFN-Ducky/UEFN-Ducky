import threading

from frontend.ui_web.terminal import focus_guard


def test_runs_share_one_lock_and_the_last_one_out_unlocks(monkeypatch):
    # Nov 1 2026: an agent's test windows took the person's typing while they wrote in Ducky.
    calls: list[bool] = []
    monkeypatch.setattr(focus_guard, "_set_locked", lambda on: calls.append(on) or True)
    with focus_guard.no_focus_steal():
        with focus_guard.no_focus_steal():
            assert calls == [True]
        assert calls == [True]
    assert calls == [True, False]


def test_an_error_inside_still_unlocks(monkeypatch):
    calls: list[bool] = []
    monkeypatch.setattr(focus_guard, "_set_locked", lambda on: calls.append(on) or True)
    try:
        with focus_guard.no_focus_steal():
            raise RuntimeError("command failed")
    except RuntimeError:
        pass
    assert calls == [True, False] and focus_guard._holders == 0


def test_concurrent_terminals_keep_the_lock_until_both_finish(monkeypatch):
    calls: list[bool] = []
    monkeypatch.setattr(focus_guard, "_set_locked", lambda on: calls.append(on) or True)
    inside, release = threading.Event(), threading.Event()

    def other_terminal():
        with focus_guard.no_focus_steal():
            inside.set()
            release.wait(2)

    t = threading.Thread(target=other_terminal)
    t.start()
    inside.wait(2)
    with focus_guard.no_focus_steal():
        pass
    assert calls == [True]  # the other terminal's command still runs
    release.set()
    t.join(2)
    assert calls == [True, False]


def test_the_real_call_is_harmless_when_ducky_is_not_in_front():
    # Not the foreground process here (a test run): Windows refuses, nothing raises.
    with focus_guard.no_focus_steal():
        pass
    assert focus_guard._holders == 0
