"""IDE hookups are owned by Store gateway plugins (not the MCPs tab)."""

from __future__ import annotations

from unittest.mock import patch


def test_plugin_api_http_conduit(tmp_path):
    from backend.uefn_plugins.host import _PluginApi
    from backend.util import http as http_util

    api = _PluginApi("conduit")
    png = tmp_path / "a.png"
    png.write_bytes(b"\x89PNG\r\n\x1a\n")
    assert api.encode_image(str(png)) == http_util.encode_image(str(png))
    assert api.resolve_image("https://example.com/a.png") == "https://example.com/a.png"
    assert callable(api.http_json) and callable(api.poll)


def test_ide_hookup_register_and_clear():
    from backend.uefn_plugins import host as h

    h.invalidate_plugin_runtime("cursor")
    assert h.get_ide_hookup("cursor") is None

    api = h._PluginApi("cursor")  # type: ignore[attr-defined]
    with patch.object(api, "log"):
        with patch("frontend.ide_apply.apply_ide_bridge", return_value="C:/fake/mcp.json"):
            api.register_ide_hookup("cursor", label="Cursor", auto_apply=True)

    with patch("backend.uefn_plugins.host.is_plugin_enabled", return_value=True):
        row = h.get_ide_hookup("cursor")
    assert row is not None
    assert row["plugin_id"] == "cursor"
    assert row["label"] == "Cursor"

    h.invalidate_plugin_runtime("cursor")
    assert h.get_ide_hookup("cursor") is None


def test_apply_all_prefers_registered_kinds():
    from frontend.ide_apply import ALL_IDES, _active_ide_kinds
    from frontend.ide_paths import IdeKind

    with patch(
        "backend.uefn_plugins.host.get_contributions",
        return_value={"ide_hookups": [{"kind": "cursor", "plugin_id": "cursor"}]},
    ):
        kinds = _active_ide_kinds()
    assert kinds == (IdeKind.CURSOR,)

    # Empty hookups (plugins still loading) must not skip startup Apply.
    with patch(
        "backend.uefn_plugins.host.get_contributions",
        return_value={"ide_hookups": []},
    ):
        kinds = _active_ide_kinds()
    assert kinds == ALL_IDES


def test_merged_ide_hookups_prefers_register_over_manifest():
    from backend.uefn_plugins import host as h

    with h._LOCK:
        h._CONTRIBUTIONS["ide_hookups"] = [
            {"kind": "cursor", "label": "FromManifest", "plugin_id": "cursor"},
        ]
        h._IDE_HOOKUPS["cursor"] = {"plugin_id": "cursor", "label": "FromRegister"}
        rows = h._merged_ide_hookups({"cursor"})
    assert rows == [{"kind": "cursor", "plugin_id": "cursor", "label": "FromRegister"}]
    with h._LOCK:
        h._CONTRIBUTIONS["ide_hookups"] = []
        h._IDE_HOOKUPS.pop("cursor", None)
