"""The settings schema must describe every PanelSettings field (no drift)."""

from __future__ import annotations

from frontend.settings import PanelSettings
from frontend.settings_schema import (
    known_field_names,
    missing_meta_fields,
    settable_keys,
    settings_schema,
)


def test_every_field_has_meta():
    # Adding a PanelSettings field without a FIELD_META entry fails here.
    assert missing_meta_fields() == set()


def test_schema_covers_all_fields():
    fields = settings_schema()["fields"]
    assert set(fields) == known_field_names()


def test_settable_keys_are_known():
    assert settable_keys().issubset(known_field_names())


def test_enum_defaults_are_valid():
    defaults = PanelSettings()
    for name, field in settings_schema()["fields"].items():
        enum = field.get("enum")
        if not enum:
            continue
        value = getattr(defaults, name)
        # Empty-string defaults mean "unset"; enum only constrains real values.
        if value == "":
            continue
        assert value in enum, f"{name} default {value!r} not in {enum}"


def test_live_voice_fields_are_settable():
    fields = settings_schema()["fields"]
    assert fields["voice_live_model"]["settable"] is True
    assert fields["voice_live_model"]["tab"] == "Audio"
    assert fields["voice_reply_length"]["enum"] == ["short", "normal", "detailed"] or tuple(
        fields["voice_reply_length"]["enum"]
    ) == ("short", "normal", "detailed")
    assert PanelSettings().voice_live_model == ""
    assert PanelSettings().voice_reply_length == "short"


def test_settings_set_accepts_live_voice_model(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setenv("APPDATA", str(tmp_path))
    from backend.tools.panel.panel_settings import apply_settings_patch

    out = apply_settings_patch(
        {"voice_live_model": "claude_code:claude-haiku-5-5", "voice_reply_length": "normal"}
    )
    assert "error" not in out, out
    saved = PanelSettings.load()
    assert saved.voice_live_model == "claude_code:claude-haiku-5-5"
    assert saved.voice_reply_length == "normal"
    assert "error" in apply_settings_patch({"voice_reply_length": "endless"})


def test_reply_length_validates_to_short():
    s = PanelSettings()
    s.voice_reply_length = "rambling"
    s.validate()
    assert s.voice_reply_length == "short"


def test_fresh_install_shares_one_tool_server(monkeypatch):
    # Off, each running agent got its own ~0.6 GB tool server (Nov 4 2026, 300 seats: PC commit full).
    from backend.bridge import shared_mcp

    monkeypatch.delenv("UEFN_DUCKY_SHARED_MCP", raising=False)
    monkeypatch.delenv(shared_mcp.SLOT_ENV, raising=False)
    assert PanelSettings().shared_mcp is True
    assert shared_mcp.enabled(PanelSettings())
