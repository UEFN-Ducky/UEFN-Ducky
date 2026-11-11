"""bind_with_fallback: a refused (10013) or busy panel UI port moves to the next free pair."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from frontend import settings


def _err(winerror: int, errno: int, text: str) -> OSError:
    exc = OSError(errno, text)
    exc.winerror = winerror
    return exc


_REFUSED = {
    "en": lambda: _err(10013, 13, "An attempt was made to access a socket in a way forbidden by its access permissions"),
    "fr": lambda: _err(10013, 13, "Une tentative d’accès à un socket de manière interdite par ses autorisations d’accès a été tentée"),
}


@pytest.fixture
def app_dir(monkeypatch, tmp_path: Path) -> Path:
    monkeypatch.setattr(settings, "default_app_data_dir", lambda: tmp_path)
    monkeypatch.setattr(settings, "PANEL_LISTENER_PORT", 4200)
    monkeypatch.setattr(settings, "_port_reserved", lambda port: False)
    monkeypatch.setenv(settings._PORT_PIN_ENV, "")
    monkeypatch.delenv(settings._PORT_PIN_ENV)
    return tmp_path


def _maker(blocked: dict[int, OSError]):
    def make(port: int) -> int:
        if port in blocked:
            raise blocked[port]
        return port  # stands in for the server

    return make


@pytest.mark.parametrize("lang", sorted(_REFUSED))
def test_refused_4199_listens_on_the_next_pair(app_dir: Path, lang: str) -> None:
    assert settings.bind_with_fallback(_maker({4199: _REFUSED[lang]()}), 4199) == 4299
    assert settings.PANEL_LISTENER_PORT == 4300
    assert json.loads((app_dir / settings.PANEL_PORT_FILE).read_text(encoding="utf-8")) == {"port": 4300}


def test_pin_and_10013_still_moves(app_dir: Path, monkeypatch) -> None:
    monkeypatch.setenv(settings._PORT_PIN_ENV, "4200")
    assert settings.bind_with_fallback(_maker({4199: _REFUSED["en"](), 4299: _REFUSED["fr"]()}), 4199) == 4399
    assert settings.PANEL_LISTENER_PORT == 4400


def test_in_use_moves_unpinned_only(app_dir: Path, monkeypatch) -> None:
    busy = _err(10048, 98, "Only one usage of each socket address is normally permitted")
    assert settings.bind_with_fallback(_maker({4199: busy}), 4199) == 4299
    monkeypatch.setenv(settings._PORT_PIN_ENV, "4200")
    with pytest.raises(OSError):
        settings.bind_with_fallback(_maker({4199: busy}), 4199)


def test_a_second_copy_on_a_busy_port_leaves_the_shared_file_alone(app_dir: Path) -> None:
    """Nov 11 2026: a second copy found 4199 taken by the running app, moved to 4299 and saved
    4300 for everyone: each tool server the app started next sent its reloads, events and panel
    calls to 4299, where nothing listened."""
    busy = _err(10048, 98, "Only one usage of each socket address is normally permitted")
    assert settings.bind_with_fallback(_maker({4199: busy}), 4199) == 4299
    assert settings.PANEL_LISTENER_PORT == 4300  # this copy and its children move
    assert not (app_dir / settings.PANEL_PORT_FILE).exists()  # every other process stays with the app


def test_app_listener_ports_try_the_saved_and_default_port_unpinned_only(app_dir: Path, monkeypatch) -> None:
    (app_dir / settings.PANEL_PORT_FILE).write_text(json.dumps({"port": 4300}), encoding="utf-8")
    monkeypatch.setattr(settings, "PANEL_LISTENER_PORT", 4300)
    assert settings.app_listener_ports() == [4300, 4200]
    monkeypatch.setenv(settings._PORT_PIN_ENV, "4300")
    assert settings.app_listener_ports() == [4300]  # a test session never reaches the running app


def test_free_port_binds_and_writes_nothing(app_dir: Path) -> None:
    assert settings.bind_with_fallback(_maker({}), 4199) == 4199
    assert not (app_dir / settings.PANEL_PORT_FILE).exists()


def test_reserved_listener_side_is_skipped(app_dir: Path, monkeypatch) -> None:
    monkeypatch.setattr(settings, "_port_reserved", lambda port: port == 4300)
    assert settings.bind_with_fallback(_maker({4199: _REFUSED["en"]()}), 4199) == 4399
