"""wire_verse_device_array accepts 1..N targets — no Unreal required."""

from __future__ import annotations

import re
import sys
import types
from pathlib import Path
from typing import Any, List

import pytest

_SRC = (Path(__file__).resolve().parent / "verse_editable_editor.py").read_text(encoding="utf-8")


def _exec_fn(name: str, ns: dict) -> None:
    match = re.search(rf"(\ndef {name}\(.*?(?=\ndef [a-zA-Z_]))", _SRC, flags=re.S)
    assert match, f"{name} not found"
    exec(match.group(1), ns)


class _Actor:
    def __init__(self, label: str) -> None:
        self.label = label
        self.modified = 0

    def get_actor_label(self) -> str:
        return self.label

    def get_path_name(self) -> str:
        return f"/Level/{self.label}"

    def modify(self) -> None:
        self.modified += 1


class _Wrapper:
    _n = 0

    def __init__(self) -> None:
        _Wrapper._n += 1
        self.props: dict[str, Any] = {}
        self.name = f"button_device_{_Wrapper._n}"

    def set_editor_property(self, k: str, v: Any) -> None:
        self.props[k] = v

    def get_editor_property(self, k: str) -> Any:
        return self.props.get(k)

    def get_fname(self) -> str:
        return self.name

    def modify(self) -> None:
        pass


class _Script:
    def __init__(self) -> None:
        self.props: dict[str, Any] = {}

    def get_editor_property(self, k: str) -> Any:
        return self.props.get(k)

    def set_editor_property(self, k: str, v: Any) -> None:
        self.props[k] = v

    def modify(self) -> None:
        pass


class _Txn:
    def __init__(self, *_a: Any) -> None:
        pass

    def __enter__(self) -> None:
        return None

    def __exit__(self, *_a: Any) -> None:
        return None


def _load(monkeypatch, targets: dict[str, _Actor]):
    marks: list[dict] = []
    overrides = types.ModuleType("listener.script_property_overrides")

    def _mark(device, *, script=None, scalar_prop=None, array_prop=None, **_kw):
        marks.append({"device": device, "array_prop": array_prop, "scalar_prop": scalar_prop})

    overrides.mark_verse_wiring_overrides = _mark  # type: ignore[attr-defined]
    pkg = types.ModuleType("listener")
    pkg.script_property_overrides = overrides  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "listener", pkg)
    monkeypatch.setitem(sys.modules, "listener.script_property_overrides", overrides)

    device = _Actor("MyDevice")
    script = _Script()
    unreal = types.SimpleNamespace(ScopedEditorTransaction=_Txn, new_object=lambda cls, outer: _Wrapper())
    lookup = types.SimpleNamespace(require_actor=lambda p: targets[p])
    ns: dict = {
        "List": List,
        "unreal": unreal,
        "lookup": lookup,
        "_require_field_for_wire": lambda a, f: (device, script, "__verse_0xABCD_Markers"),
        "_is_array_field": lambda a, f: True,
        "_wrapper_spec_for_field": lambda a, f: ("/Script/Fortnite.ButtonDevice", "SavedActor"),
        "_load_verse_class": lambda p: object(),
    }
    _exec_fn("wire_verse_device_array", ns)
    return ns["wire_verse_device_array"], script, marks


def test_three_targets_wired_in_one_call(monkeypatch):
    targets = {n: _Actor(n) for n in ("M1", "M2", "M3")}
    fn, script, marks = _load(monkeypatch, targets)
    out = fn("MyDevice", "Markers", ["M1", "M2", "M3"])
    assert out["wired"] == ["M1", "M2", "M3"]
    assert out["count"] == 3
    assert out["ok"] is True
    assert [link["target"] for link in out["links"]] == ["M1", "M2", "M3"]
    assert len(script.props["__verse_0xABCD_Markers"]) == 3
    assert marks == [{"device": marks[0]["device"], "array_prop": "__verse_0xABCD_Markers", "scalar_prop": None}]


def test_single_target_still_works(monkeypatch):
    fn, _script, _marks = _load(monkeypatch, {"M1": _Actor("M1")})
    out = fn("MyDevice", "Markers", ["M1"])
    assert out["wired"] == ["M1"] and out["count"] == 1


def test_zero_targets_rejected(monkeypatch):
    fn, _script, _marks = _load(monkeypatch, {})
    with pytest.raises(ValueError, match="at least one"):
        fn("MyDevice", "Markers", [])
