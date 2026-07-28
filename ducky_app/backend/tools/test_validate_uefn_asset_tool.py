"""MCP wrapper for validate_uefn_asset forwards expected args."""

from __future__ import annotations

from unittest.mock import patch

from backend.tools import assets_pipeline


def test_validate_uefn_asset_forwards_params():
    with patch("backend.tools.assets_pipeline.send_command") as send:
        send.return_value = {"ok": True, "valid": True}
        out = assets_pipeline.validate_uefn_asset(
            "/Roguelike/VFX/NS_Sploder_Explosion",
            usecase="SCRIPT",
            pretty=False,
        )
    send.assert_called_once_with(
        "validate_uefn_asset",
        {
            "asset_path": "/Roguelike/VFX/NS_Sploder_Explosion",
            "usecase": "SCRIPT",
        },
    )
    assert "valid" in out


def test_open_asset_in_uefn_forwards_params():
    from backend.tools import assets

    with patch("backend.tools.assets.send_command") as send:
        send.return_value = {"success": True, "opened": True}
        out = assets.open_asset_in_uefn(
            "/Roguelike/VFX/NS_Sploder_Explosion",
            open_editor=False,
            pretty=False,
        )
    send.assert_called_once_with(
        "open_asset_in_uefn",
        {
            "asset_path": "/Roguelike/VFX/NS_Sploder_Explosion",
            "open_editor": False,
        },
    )
    assert "success" in out
