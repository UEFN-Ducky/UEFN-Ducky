"""plugin.json contribution rows: one row normalized, icons resolved, a plugin's rows stripped."""

from __future__ import annotations

from backend.uefn_plugins.plugin_host import contributions, state


def test_automation_row_needs_an_id_and_names_its_plugin() -> None:
    assert contributions._automation_contrib_row({"label": "no id"}, "p") is None
    assert contributions._automation_contrib_row({"type": "email.send"}, "p") == {
        "type": "email.send", "id": "email.send", "plugin_id": "p"}


def test_image_generator_row_needs_a_tool_and_keeps_its_cost() -> None:
    assert contributions._image_generator_row({"id": "g"}, "p") is None
    row = contributions._image_generator_row({"id": "g", "tool": "p_make", "credits": "3", "paid": True}, "p")
    assert row["credits"] == 3 and row["paid"] is True and row["prompt_arg"] == "prompt"


def test_media_backend_row_skips_text_to_image_and_maps_pins() -> None:
    assert contributions._media_backend_row({"id": "g", "tool": "t", "node": "image.generate"}, "p") is None
    row = contributions._media_backend_row({"id": "g", "tool": "t", "node": "mesh.rig", "pins": {"mesh": "model"}}, "p")
    assert row["node"] == "mesh.rig" and row["pins"] == {"mesh": "model"} and "prompt_arg" not in row


def test_strip_removes_only_that_plugins_rows() -> None:
    with state._LOCK:
        state._CONTRIBUTIONS["sounds"].extend([{"id": "a", "plugin_id": "strip-a"}, {"id": "b", "plugin_id": "strip-b"}])
        state._CONTRIBUTIONS["agent_tools"]["strip-a"] = {"tools": []}
        try:
            contributions._strip_contributions_for("strip-a")
            pids = {r.get("plugin_id") for r in state._CONTRIBUTIONS["sounds"]}
            assert "strip-a" not in pids and "strip-b" in pids
            assert "strip-a" not in state._CONTRIBUTIONS["agent_tools"]
        finally:
            contributions._strip_contributions_for("strip-b")


def test_icons_inline_a_shipped_asset_and_keep_urls(tmp_path) -> None:
    (tmp_path / "assets").mkdir()
    (tmp_path / "assets" / "icon.svg").write_text("<svg/>", encoding="utf-8")
    assert contributions._resolve_contrib_icon("https://x/icon.png", tmp_path) == "https://x/icon.png"
    assert contributions._resolve_contrib_icon("", tmp_path).startswith("data:image/svg+xml;base64,")
    assert contributions._resolve_contrib_icon("🦆", tmp_path) == "🦆"
