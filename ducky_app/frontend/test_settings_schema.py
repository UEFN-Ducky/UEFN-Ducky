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
