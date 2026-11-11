"""A Windows-reserved panel port (WinError 10013) moves Ducky to a free pair instead of stopping startup."""

from __future__ import annotations

import json
from pathlib import Path

from frontend import settings


def _setup(monkeypatch, tmp_path: Path, reserved: set[int]) -> None:
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setenv("UEFN_DUCKY_PANEL_PORT", "")  # records the real value, so pick's write is undone
    monkeypatch.delenv("UEFN_DUCKY_PANEL_PORT")
    monkeypatch.setattr(settings, "default_app_data_dir", lambda: tmp_path / "UEFN-Ducky")
    monkeypatch.setattr(settings, "PANEL_LISTENER_PORT", 4200)
    monkeypatch.setattr(settings, "_port_reserved", lambda port: port in reserved)


def test_free_default_pair_stays_and_writes_nothing(monkeypatch, tmp_path: Path) -> None:
    _setup(monkeypatch, tmp_path, reserved=set())
    assert settings.pick_panel_port() == 4200
    assert not (tmp_path / "UEFN-Ducky" / settings.PANEL_PORT_FILE).exists()


def test_reserved_ui_port_moves_the_pair_and_saves_it(monkeypatch, tmp_path: Path) -> None:
    _setup(monkeypatch, tmp_path, reserved={4199, 4299})
    assert settings.pick_panel_port() == 4400
    assert settings.PANEL_LISTENER_PORT == 4400
    import os

    assert os.environ["UEFN_DUCKY_PANEL_PORT"] == "4400"
    saved = json.loads((tmp_path / "UEFN-Ducky" / settings.PANEL_PORT_FILE).read_text(encoding="utf-8"))
    assert saved == {"port": 4400}
    assert settings._saved_panel_port() == 4400


def test_test_sessions_keep_their_port(monkeypatch, tmp_path: Path) -> None:
    _setup(monkeypatch, tmp_path, reserved={4199})
    monkeypatch.setenv("UEFN_DUCKY_PANEL_PORT", "5555")
    monkeypatch.setattr(settings, "PANEL_LISTENER_PORT", 5555)
    assert settings.pick_panel_port() == 5555


def test_port_in_use_is_not_reserved() -> None:
    import socket

    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        sock.listen(1)
        assert settings._port_reserved(sock.getsockname()[1]) is False


def test_a_moved_panel_pair_leaves_the_uefn_listener_port_alone(monkeypatch, tmp_path: Path) -> None:
    """Nov 11 2026: the panel moved to 4299/4300 and every bridge then looked for UEFN on 4300."""
    from frontend import mcp_block

    _setup(monkeypatch, tmp_path, reserved={4199})
    assert settings.pick_panel_port() == 4300
    s = settings.PanelSettings(port=4300)
    args = mcp_block.resolve_bridge_args(s)
    assert args[args.index("--port") + 1] == "4200"
    assert mcp_block.build_uefn_server_block(s)["env"]["UEFN_DUCKY_PORT"] == "4200"
