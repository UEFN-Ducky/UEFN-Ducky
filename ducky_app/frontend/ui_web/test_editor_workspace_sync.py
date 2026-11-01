from frontend.ui_web import panel_api  # noqa: F401 — loads panel_api_window without the import cycle
from frontend.ui_web import agent_modes
from frontend.ui_web import editor_workspace
from frontend.ui_web.panel_api_window import PanelApiWindowMixin


def test_saving_tabs_tells_every_other_page_and_keeps_the_origin_out_of_the_file(monkeypatch):
    # Nov 1 2026: a tab closed on the phone stayed open on the PC until a restart.
    saved, pushed = [], []
    monkeypatch.setattr(editor_workspace, "save_editor_workspace", lambda payload: saved.append(payload))
    monkeypatch.setattr(agent_modes, "push_ui_event", lambda event: pushed.append(event))
    api = PanelApiWindowMixin()
    api.save_editor_workspace({"version": 1, "openTabs": [], "origin": "phone-1"})
    assert saved == [{"version": 1, "openTabs": []}]
    assert pushed == [{"type": "editor_workspace_changed", "origin": "phone-1"}]

    api.save_editor_workspace({"version": 1, "openTabs": []})  # an old page: saved, nobody told
    assert len(saved) == 2 and len(pushed) == 1
