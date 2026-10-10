"""Verse editable resolve helpers — no Unreal required."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

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


def test_resolve_for_wire_falls_back_to_export_text():
    ns = _load_script_verse_properties()
    trigger = "__verse_0xA4892A98_Trigger"
    array_prop = "__verse_0x725305C1_TestProps"
    script = _FakeScript(
        props={trigger: "btn", array_prop: ["w0"]},
        export=f"Begin Object\n   {array_prop}(0)=...\nEnd Object\n",
        dir_names=[trigger],
    )
    verse_text = "@editable Trigger : button_device = ...\n@editable TestProps : []creative_prop = array{}"

    table = []
    for i in range(256):
        c = i
        for _ in range(8):
            c = (c >> 1) ^ 0xEDB88320 if c & 1 else c >> 1
        table.append(c)
    ns["_CRC_TABLE"] = table
    ns["_FIELD_NOT_READABLE"] = "not readable"
    ns["_SCRIPT_PROPS_CACHE"] = {}
    _exec_fn("verse_mangled_name", ns)
    ns["_verse_source_for_actor"] = lambda _actor: ("cls", verse_text, "x.verse")
    ns["_EDITABLE_RE"] = re.compile(
        r"^\s*@editable(?:\s+<[^>]+>)?\s*\n\s*([A-Za-z_][A-Za-z0-9_]*)(?:\s*<[^>]+>)*\s*:",
        re.MULTILINE,
    )
    ns["_EDITABLE_INLINE_RE"] = re.compile(
        r"@editable\s+(?:<[^>]+>\s+)?([A-Za-z_][A-Za-z0-9_]*)(?:\s*<[^>]+>)?"
    )
    _exec_fn("_parse_editables_from_verse", ns)
    ns["_field_not_found_error"] = lambda *_a, **_k: "not found"
    _exec_fn("_hash_not_readable", ns)
    _exec_fn("_remember_resolved_prop", ns)
    _exec_fn("_resolve_field_prop_for_wire", ns)

    prop = ns["_resolve_field_prop_for_wire"](object(), script, "TestProps")
    assert prop == array_prop


def test_verse_mangled_name_known_pairs():
    table = []
    for i in range(256):
        c = i
        for _ in range(8):
            c = (c >> 1) ^ 0xEDB88320 if c & 1 else c >> 1
        table.append(c)
    ns: dict = {"_CRC_TABLE": table}
    _exec_fn("verse_mangled_name", ns)
    fn = ns["verse_mangled_name"]
    known = {
        "Trigger": "A4892A98",
        "PlayerManager": "8E1BF1DC",
        "AllPlayerSpawners": "E027EF92",
        "HittableButton0": "888A0FAC",
        "HittableButton1": "EDEDB314",
        "FeedbackDisplay": "05586E45",
        "CurrencyConfigs": "00206A09",
        "CatSpawners": "DE71A4D4",
    }
    for field, hexhash in known.items():
        assert fn(field) == f"__verse_0x{hexhash}_{field}", field


def test_get_verse_editables_computes_mangled_name():
    start = _SRC.index("def get_verse_editables")
    end = _SRC.index("\ndef set_verse_editable")
    body = _SRC[start:end]
    assert "verse_mangled_name" in body
    assert "_class_scoped_hash_scan" not in body
    assert "_lookup_field_hash_in_dirs" not in body
    assert "hash_source" in body
    assert "readable" in body
    assert '"forbidden_until_compiled": []' in body


def test_set_currency_config_entries_writes_extra_keys():
    start = _SRC.index("def set_currency_config_entries")
    body = _SRC[start : start + 1800]
    assert 'if key in ("name", "CurrencyName", "display_order", "DisplayOrder")' in body
    assert "_mangled_name(str(key))" in body


def _load_struct_resolver():
    """``_resolve_verse_struct_class`` for a struct Verse has not built yet, on a fake clock."""
    rescans: List[bool] = []
    clock = {"now": 1000.0}

    class _Registry:
        def search_all_assets(self, synchronous: bool) -> None:
            rescans.append(synchronous)

    class _Unreal:
        class AssetRegistryHelpers:
            @staticmethod
            def get_asset_registry() -> _Registry:
                return _Registry()

        @staticmethod
        def load_class(_outer: Any, _path: str) -> None:
            return None

    class _Clock:
        time = staticmethod(lambda: clock["now"])
        monotonic = staticmethod(lambda: clock["now"])

    ns: dict = {
        "Any": Any, "Dict": Dict, "List": List, "Optional": Optional, "unreal": _Unreal, "time": _Clock,
        "_WIRING_READY_ACTORS": set(), "_VERSE_SOURCE_CACHE": {}, "_FIELD_TYPE_CACHE": {},
        "_SCRIPT_PROPS_CACHE": {}, "_FIELD_SNIPPET_CACHE": {}, "_VERSE_SEARCH_DIRS_CACHE": None,
    }
    # The struct cache and anything else the module keeps for it, as declared there.
    for line in re.findall(r"^_STRUCT_\w+.*= .*$", _SRC, flags=re.M):
        exec(line, ns)
    ns["_find_verse_struct_class_path"] = lambda _key: None
    ns["_computed_hashes_from_verse"] = lambda: {}
    _exec_fn("_resolve_verse_struct_class", ns)
    _exec_fn("list_verse_property_hashes", ns)
    return ns, rescans, clock


def test_a_missing_struct_rescans_the_registry_at_most_once_a_minute():
    ns, rescans, clock = _load_struct_resolver()
    resolve = ns["_resolve_verse_struct_class"]

    for _ in range(3):
        with pytest.raises(ValueError, match="Recompile Verse"):
            resolve("player_level_threshold")
    assert rescans == [True]

    clock["now"] += 61
    with pytest.raises(ValueError):
        resolve("player_level_threshold")
    assert rescans == [True, True]

    # A refresh (after a Verse build) lets the next miss rescan straight away.
    ns["list_verse_property_hashes"](refresh=True)
    with pytest.raises(ValueError):
        resolve("player_level_threshold")
    assert rescans == [True, True, True]
