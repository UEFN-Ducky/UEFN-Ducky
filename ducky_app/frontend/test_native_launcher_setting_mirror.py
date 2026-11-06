"""PanelSettings.save keeps launcher/native_launcher.json (what Ducky-Launcher.exe reads) in step."""

from __future__ import annotations

import json

import pytest

from frontend.settings import LAUNCHER_SETTING_FILE, PanelSettings, default_app_data_dir


def _mirror() -> dict | None:
    path = default_app_data_dir().joinpath(*LAUNCHER_SETTING_FILE)
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None


@pytest.mark.parametrize("backend", ["db", "files"])
def test_save_mirrors_the_setting_for_the_launcher(monkeypatch: pytest.MonkeyPatch, backend: str) -> None:
    if backend == "files":
        monkeypatch.setenv("DUCKY_STORE_BACKEND", "files")
    else:
        monkeypatch.delenv("DUCKY_STORE_BACKEND", raising=False)
        monkeypatch.delenv("DUCKY_STORE_BACKEND_SETTINGS", raising=False)
    default_app_data_dir().joinpath(*LAUNCHER_SETTING_FILE).unlink(missing_ok=True)
    s = PanelSettings.load()
    s.web_access = "on"
    s.save()
    assert _mirror() is None  # default auto: no file, the launcher's default is auto too
    s = PanelSettings.load()
    s.native_launcher = "off"
    s.save()
    assert _mirror() == {"native_launcher": "off"}
    s = PanelSettings.load()
    assert s.native_launcher == "off"
    s.native_launcher = "auto"
    s.save()
    assert _mirror() == {"native_launcher": "auto"}  # back to auto overwrites a saved off
    assert not list(default_app_data_dir().joinpath(LAUNCHER_SETTING_FILE[0]).glob("*.tmp"))
