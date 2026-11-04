"""PanelApi.set_visible_terminals reaches the terminal manager's pacers."""

from __future__ import annotations

from frontend.ui_web import panel_api
from frontend.ui_web.lag.output_pacer import OutputPacer
from frontend.ui_web.terminal import manager as manager_mod


def test_window_report_hides_the_terminals_it_does_not_show(monkeypatch) -> None:
    mgr = manager_mod.TerminalManager()
    for sid in ("t1", "t2", "t3"):
        entry = manager_mod._SessionEntry.__new__(manager_mod._SessionEntry)
        entry.pacer = OutputPacer(lambda d: None, lambda s: None, hub=None)
        mgr._sessions[sid] = entry
    monkeypatch.setattr(manager_mod, "get_terminal_manager", lambda: mgr)
    api = panel_api.PanelApi.__new__(panel_api.PanelApi)

    assert api.set_visible_terminals(["t2", ""], "") is True
    shown = {sid: e.pacer.visible for sid, e in mgr._sessions.items()}
    assert shown == {"t1": False, "t2": True, "t3": False}  # 3 open, 1 shown: 30 + 1 + 1 frames/s

    api.set_visible_terminals([], "main")  # window minimised
    assert not any(e.pacer.visible for e in mgr._sessions.values())
