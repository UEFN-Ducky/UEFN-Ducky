"""ducky_verse_template_apply with a Store id says which tool or plugin to use."""

from __future__ import annotations


def test_plugin_ids_name_the_tool_or_the_plugin_to_turn_on(monkeypatch):
    # Oct 28 2026: verse_template_apply failed 5 of 5; plugin:… ids hit the custom-only tool.
    from backend.tools.panel import panel_verse_templates as pvt
    from backend.uefn_plugins import host

    rows = [{"id": "player_core", "plugin_id": "uefn", "name": "PlayerCore"}]
    monkeypatch.setattr(host, "get_contributions", lambda: {"verse_templates": rows})

    on = pvt.apply_user_verse_template("plugin:uefn:player_core")
    assert on["ok"] is False
    assert "verse_template_apply(template_id='plugin:uefn:player_core')" in on["error"]
    assert "plugin 'uefn'" in pvt.apply_user_verse_template("player_core")["error"]

    monkeypatch.setattr(host, "get_contributions", lambda: {"verse_templates": []})
    off = pvt.apply_user_verse_template("plugin:uefn:player_core")
    assert "turn 'uefn' on in the Store" in off["error"]
    assert "unknown template_id: nope" in pvt.apply_user_verse_template("nope")["error"]
