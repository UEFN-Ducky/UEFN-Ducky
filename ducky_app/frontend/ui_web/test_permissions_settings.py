"""User permissions must round-trip through the same API used by General."""
from frontend.settings import PanelSettings
from frontend.ui_web import panel_api as pa
from frontend.ui_web.panel_api_settings import PanelApiSettingsMixin


def test_ai_access_permissions_round_trip_without_touching_real_settings(monkeypatch):
    settings = PanelSettings()
    settings.uefn_project_root = ""
    monkeypatch.setattr(pa.PanelSettings, "load", classmethod(lambda cls: settings))
    saved = []
    monkeypatch.setattr(pa.PanelSettings, "save", lambda self: saved.append(self))
    monkeypatch.setattr(pa, "apply_workspace_env", lambda _root: None)
    api = PanelApiSettingsMixin()
    expected = {
        "allow_settings_write": False,
        "allow_agent_clicks": True,
        "allow_see_uefn": False,
        "allow_see_other_programs": False,
    }
    assert api.save_agent_settings(expected).startswith("Saved")
    assert saved == [settings]
    result = api.get_settings()
    assert {key: result[key] for key in expected} == expected
    assert settings.ai_ignore_strict is True
