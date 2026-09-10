"""Refuse actor scale on Fortnite Creative devices — no Unreal required."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

MODULE = Path(__file__).resolve().parent / "device_scale.py"


def _load():
    spec = importlib.util.spec_from_file_location("device_scale_under_test", MODULE)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


def test_creative_device_scale_refused():
    refuse = _load().refuse_if_creative_device_scale
    with pytest.raises(ValueError, match="never scale Fortnite Creative devices"):
        refuse("creative_device", [2.0, 2.0, 2.0])


def test_prop_scale_allowed():
    _load().refuse_if_creative_device_scale("actor", [2.0, 2.0, 2.0])


def test_verse_script_scale_allowed():
    _load().refuse_if_creative_device_scale("verse_script", [1.5, 1.5, 1.5])


def test_device_location_only_allowed():
    _load().refuse_if_creative_device_scale("creative_device", None)
