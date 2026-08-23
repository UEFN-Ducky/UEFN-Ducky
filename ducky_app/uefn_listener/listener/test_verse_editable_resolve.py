"""Verse editable resolve helpers — no Unreal required."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, List, Optional

_SRC = (Path(__file__).resolve().parent / "verse_editable_editor.py").read_text(
    encoding="utf-8"
)


def _exec_fn(name: str, ns: dict) -> None:
    match = re.search(rf"(def {name}\(.*?(?=\ndef [a-zA-Z_]))", _SRC, flags=re.S)
    assert match, f"{name} not found"
    exec(match.group(1), ns)


def _load_wiring_readiness():
    ns: dict = {"List": List, "Dict": Dict}
    _exec_fn("_wiring_readiness", ns)
    return ns["_wiring_readiness"]


class _FakeClass:
    def get_name(self) -> str:
        return "Verse-NPCCore-catdog_spawn_controller"


class _FakeScript:
    def __init__(self, props: Dict[str, Any] | None = None, export: str = "") -> None:
        self._props = props or {}
        self._export = export

    def get_class(self) -> _FakeClass:
        return _FakeClass()

    def get_editor_property(self, name: str) -> Any:
        if name not in self._props:
            raise ValueError(f"not found: {name}")
        return self._props[name]

    def export_text(self) -> str:
        return self._export

    def __dir__(self) -> List[str]:
        return []


def _load_script_verse_properties():
    ns: dict = {
        "Any": Any,
        "Dict": Dict,
        "List": List,
        "Optional": Optional,
        "re": re,
        "_SCRIPT_PROP_RE": re.compile(r"__verse_0x[0-9A-Fa-f]{8}_(.+)"),
        "_SCRIPT_PROP_NAME_RE": re.compile(r"__verse_0x[0-9A-Fa-f]{8}_[A-Za-z0-9_]+"),
        "_SCRIPT_PROPS_CACHE": {},
    }

    class _Unreal:
        @staticmethod
        def log_warning(msg: str) -> None:
            return None

    ns["unreal"] = _Unreal()
    _exec_fn("_collect_readable_verse_props", ns)
    _exec_fn("_script_export_text_properties", ns)
    _exec_fn("_iter_class_property_names", ns)
    _exec_fn("_script_verse_properties", ns)
    return ns


def test_wiring_readiness_compile_required_only_when_empty():
    ready = _load_wiring_readiness()
    out = ready(["CatSpawners"], {})
    assert out["can_wire"] is False
    assert out["status"] == "verse_compile_required"
    assert "Do not ask the user" in out["next_step"]

    partial = ready(["CatSpawners", "DogSpawners"], {"CatSpawners": "__verse_0xAA_CatSpawners"})
    assert partial["can_wire"] is True
    assert partial["status"] == "partial"


def test_empty_reflection_is_not_cached():
    ns = _load_script_verse_properties()
    fn = ns["_script_verse_properties"]
    script = _FakeScript()
    first = fn(script)
    assert first == {}
    assert "Verse-NPCCore-catdog_spawn_controller" not in ns["_SCRIPT_PROPS_CACHE"]


def test_export_text_resolves_and_caches():
    ns = _load_script_verse_properties()
    fn = ns["_script_verse_properties"]
    mangled = "__verse_0xDE71A4D4_CatSpawners"
    script = _FakeScript(
        props={mangled: ["wrapper0"]},
        export=f"Begin Object\n   {mangled}(0)=...\nEnd Object\n",
    )
    found = fn(script)
    assert found == {"CatSpawners": mangled}
    assert ns["_SCRIPT_PROPS_CACHE"]["Verse-NPCCore-catdog_spawn_controller"] == found


def test_class_scan_used_when_cheap_pass_empty():
    start = _SRC.index("def get_verse_editables")
    end = _SRC.index("\ndef set_verse_editable")
    body = _SRC[start:end]
    assert "_class_scoped_hash_scan" in body
    assert "resolution_tried" in body
    assert '"forbidden_until_compiled": []' in body
