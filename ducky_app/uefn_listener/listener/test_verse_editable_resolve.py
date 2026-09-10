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
    def __init__(
        self,
        props: Dict[str, Any] | None = None,
        export: str = "",
        dir_names: List[str] | None = None,
    ) -> None:
        self._props = props or {}
        self._export = export
        self._dir_names = dir_names

    def get_class(self) -> _FakeClass:
        return _FakeClass()

    def get_editor_property(self, name: str) -> Any:
        if name not in self._props:
            raise ValueError(f"not found: {name}")
        return self._props[name]

    def export_text(self) -> str:
        return self._export

    def __dir__(self) -> List[str]:
        if self._dir_names is not None:
            return list(self._dir_names)
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


def test_dir_trigger_plus_export_text_array_merges_both():
    """dir() sees object-ref Trigger; TArray TestProps only lives in export-text."""
    ns = _load_script_verse_properties()
    fn = ns["_script_verse_properties"]
    trigger = "__verse_0xA4892A98_Trigger"
    array_prop = "__verse_0x725305C1_TestProps"
    script = _FakeScript(
        props={trigger: "btn", array_prop: ["w0"]},
        export=(
            "Begin Object\n"
            f"   {trigger}=...\n"
            f"   {array_prop}(0)=Devices_creative_prop_0\n"
            "End Object\n"
        ),
        dir_names=[trigger],
    )
    found = fn(script)
    assert found == {"Trigger": trigger, "TestProps": array_prop}
    assert ns["_SCRIPT_PROPS_CACHE"]["Verse-NPCCore-catdog_spawn_controller"] == found


def test_poison_cache_reenumerates_when_verse_fields_missing():
    ns = _load_script_verse_properties()
    fn = ns["_script_verse_properties"]
    trigger = "__verse_0xA4892A98_Trigger"
    array_prop = "__verse_0x725305C1_TestProps"
    cls = "Verse-NPCCore-catdog_spawn_controller"
    ns["_SCRIPT_PROPS_CACHE"][cls] = {"Trigger": trigger}
    script = _FakeScript(
        props={trigger: "btn", array_prop: ["w0"]},
        export=f"Begin Object\n   {array_prop}(0)=...\nEnd Object\n",
        dir_names=[trigger],
    )
    poisoned = fn(script)
    assert poisoned == {"Trigger": trigger}
    found = fn(script, required_fields=["Trigger", "TestProps"])
    assert found["Trigger"] == trigger
    assert found["TestProps"] == array_prop
    assert ns["_SCRIPT_PROPS_CACHE"][cls]["TestProps"] == array_prop


def test_incomplete_map_is_not_cached():
    ns = _load_script_verse_properties()
    fn = ns["_script_verse_properties"]
    trigger = "__verse_0xA4892A98_Trigger"
    script = _FakeScript(props={trigger: "btn"}, dir_names=[trigger])
    found = fn(script, required_fields=["Trigger", "TestProps"])
    assert found == {"Trigger": trigger}
    assert "Verse-NPCCore-catdog_spawn_controller" not in ns["_SCRIPT_PROPS_CACHE"]


def test_resolve_for_wire_uses_export_text_instead_of_stale():
    ns = _load_script_verse_properties()
    trigger = "__verse_0xA4892A98_Trigger"
    array_prop = "__verse_0x725305C1_TestProps"
    script = _FakeScript(
        props={trigger: "btn", array_prop: ["w0"]},
        export=f"Begin Object\n   {array_prop}(0)=...\nEnd Object\n",
        dir_names=[trigger],
    )
    verse_text = "@editable Trigger : button_device = ...\n@editable TestProps : []creative_prop = array{}"

    def _resolve_field_prop(_script, field, _hashes):
        return None

    ns["_resolve_field_prop"] = _resolve_field_prop
    ns["_cached_hashes"] = lambda: {}
    ns["_verse_source_for_actor"] = lambda _actor: ("cls", verse_text, "x.verse")
    ns["_parse_editables_from_verse"] = lambda text: (
        ns["_EDITABLE_RE"].findall(text) if text else []
    )
    ns["_EDITABLE_RE"] = re.compile(
        r"^\s*@editable(?:\s+<[^>]+>)?\s*\n\s*([A-Za-z_][A-Za-z0-9_]*)(?:\s*<[^>]+>)*\s*:",
        re.MULTILINE,
    )
    ns["_EDITABLE_INLINE_RE"] = re.compile(
        r"@editable\s+(?:<[^>]+>\s+)?([A-Za-z_][A-Za-z0-9_]*)(?:\s*<[^>]+>)?"
    )
    # Inline form in verse_text — parse_editables uses both regexes in the real fn.
    # Exec the real parser so TestProps is found.
    _exec_fn("_parse_editables_from_verse", ns)
    ns["_class_scoped_hash_scan"] = lambda _script, **_k: {}
    ns["_probe_script_for_field"] = lambda *_a, **_k: None
    ns["_lookup_field_hash_in_dirs"] = lambda *_a, **_k: None
    ns["_wire_hash_search_dirs"] = lambda _s: []
    ns["_augment_hash_cache"] = lambda *_a, **_k: None
    ns["_field_not_found_error"] = lambda *_a, **_k: "not found"
    ns["unreal"] = ns["unreal"]
    _exec_fn("_hash_not_readable", ns)
    _exec_fn("_remember_resolved_prop", ns)
    _exec_fn("_resolve_field_prop_for_wire", ns)

    prop = ns["_resolve_field_prop_for_wire"](object(), script, "TestProps")
    assert prop == array_prop


def test_class_scan_used_when_any_field_unresolved():
    start = _SRC.index("def get_verse_editables")
    end = _SRC.index("\ndef set_verse_editable")
    body = _SRC[start:end]
    assert "_class_scoped_hash_scan" in body
    assert "any(not prelim.get(f) for f in verse_fields)" in body
    assert "resolution_tried" in body
    assert '"forbidden_until_compiled": []' in body
