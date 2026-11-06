"""The native launcher switch: the setting, the decision, the fallbacks, the rollout numbers."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from frontend.app_paths import resolve_app_data_dir
from frontend.settings import PanelSettings, normalize_native_launcher


# --- d2: the native_launcher setting -------------------------------------------------


@pytest.fixture
def files_backend(monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("DUCKY_STORE_BACKEND", "files")
    path = resolve_app_data_dir() / "panel_settings.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def test_default_is_off() -> None:
    assert PanelSettings().native_launcher == "off"


def test_old_file_without_the_key_means_off(files_backend: Path) -> None:
    files_backend.write_text(json.dumps({"web_access": "on", "terminals_enabled": False}), encoding="utf-8")
    assert PanelSettings.load().native_launcher == "off"


@pytest.mark.parametrize("raw", ["garbage", "", None, 1, True, ["on"], {"on": 1}, "ON ", "Auto"])
def test_bad_value_counts_as_off_or_is_normalized(files_backend: Path, raw: object) -> None:
    files_backend.write_text(json.dumps({"web_access": "on", "native_launcher": raw}), encoding="utf-8")
    got = PanelSettings.load().native_launcher
    expected = {"ON ": "on", "Auto": "auto"}.get(raw if isinstance(raw, str) else "", "off")
    assert got == expected


def test_normalize_native_launcher() -> None:
    assert [normalize_native_launcher(v) for v in ("off", "on", "auto", "nope", None, 3)] == [
        "off", "on", "auto", "off", "off", "off",
    ]


def test_save_keeps_every_other_key_and_its_order(files_backend: Path) -> None:
    files_backend.write_text(json.dumps({"web_access": "on", "terminals_enabled": False}), encoding="utf-8")
    s = PanelSettings.load()
    before = PanelSettings.load().to_json_dict()
    s.native_launcher = "auto"
    s.save()
    after = json.loads(files_backend.read_text(encoding="utf-8"))
    assert after["native_launcher"] == "auto"
    assert after["web_access"] == "on" and after["terminals_enabled"] is False
    keys = [k for k in after if k != "native_launcher"]
    assert keys == [k for k in before if k != "native_launcher"]
    assert {k: v for k, v in after.items() if k != "native_launcher"} == {
        k: v for k, v in before.items() if k != "native_launcher"
    }
    assert PanelSettings.load().native_launcher == "auto"


def test_save_turns_garbage_into_off(files_backend: Path) -> None:
    s = PanelSettings(web_access="on")
    s.native_launcher = "warp-speed"
    s.save()
    assert json.loads(files_backend.read_text(encoding="utf-8"))["native_launcher"] == "off"


def test_database_round_trip() -> None:
    s = PanelSettings.load()
    s.native_launcher = "on"
    s.save()
    assert PanelSettings.load().native_launcher == "on"


def test_schema_describes_the_setting() -> None:
    from frontend.settings_schema import settings_schema

    entry = settings_schema()["fields"]["native_launcher"]
    assert entry["default"] == "off"
    assert entry["enum"] == ["off", "on", "auto"]
    assert entry["settable"] is False
