"""Host-side checks for device graph sim + harness parsing (no UEFN)."""

from __future__ import annotations

from pathlib import Path

from backend.testing.device_sim import device_graph_audit, simulate_device_event
from backend.testing.verse_harness import (
    add_verse_test_case,
    compare_simulation_effects,
    parse_test_results,
    save_simulation_scenario,
    load_simulation_scenario,
)


def _fixture_snapshot() -> dict:
    return {
        "nodes": [
            {
                "id": "/Game/Button",
                "label": "StartButton",
                "class": "Button_Device_C",
                "kind": "creative_device",
            },
            {
                "id": "/Game/Trigger",
                "label": "GoTrigger",
                "class": "Trigger_Device_C",
                "kind": "creative_device",
            },
            {
                "id": "/Game/Granter",
                "label": "RewardGranter",
                "class": "Item_Granter_Device_C",
                "kind": "creative_device",
            },
            {
                "id": "/Game/Orphan",
                "label": "LonelyHud",
                "class": "HUD_Message_Device_C",
                "kind": "creative_device",
            },
        ],
        "edges": [
            {
                "from": "/Game/Button",
                "to": "/Game/Trigger",
                "field": "On Interact",
                "kind": "creative_binding",
                "wired": True,
            },
            {
                "from": "/Game/Trigger",
                "to": "/Game/Granter",
                "field": "TriggeredEvent",
                "kind": "creative_binding",
                "wired": True,
            },
            {
                "from": "/Game/Orphan",
                "to": None,
                "field": "Message",
                "kind": "verse_editable",
                "wired": False,
            },
        ],
    }


def test_simulate_button_to_granter_chain():
    result = simulate_device_event(_fixture_snapshot(), "StartButton", "InteractedWithEvent")
    assert result["ok"] is True
    devices = [s["device"] for s in result["trace"]]
    assert "StartButton" in devices
    assert "GoTrigger" in devices
    assert "RewardGranter" in devices
    kinds = {e["kind"] for e in result["effects"]}
    assert "grant_item" in kinds


def test_load_semantics_has_no_disk_dependency():
    from backend.testing.device_sim import load_semantics

    load_semantics.cache_clear()
    catalog = load_semantics()
    assert "button_device" in catalog
    assert "verse_script" in catalog


def test_workspace_source_sim_is_shallow_ok():
    snap = {
        "nodes": [
            {
                "id": "verse://Verse/foo.verse#dungeon_level",
                "label": "dungeon_level",
                "class": "dungeon_level",
                "kind": "verse_source",
            }
        ],
        "edges": [],
    }
    result = simulate_device_event(snap, "dungeon_level", "InteractedWithEvent")
    assert result["ok"] is True
    assert result["trace"]
    assert "Source-only" in (result.get("note") or "")


def test_simulate_missing_device():
    result = simulate_device_event(_fixture_snapshot(), "NoSuchDevice")
    assert result["ok"] is False
    assert "not found" in result["error"]


def test_audit_finds_unwired_and_orphan():
    audit = device_graph_audit(_fixture_snapshot())
    assert audit["ok"] is True
    assert any(u["field"] == "Message" for u in audit["unwired"])
    assert any(o["label"] == "LonelyHud" for o in audit["orphans"])
    codes = {i["code"] for i in audit["issues"]}
    assert "unwired_refs" in codes
    assert "orphan_devices" in codes
    assert "no_spawn_pads" in codes


def test_parse_ducky_test_results():
    lines = [
        "LogVerse: [DUCKY-TEST] PASS leveling.xp_per_level: got 100",
        "LogVerse: Display: noise",
        "LogVerse: [DUCKY-TEST] FAIL movement.walk_speed: 50.0 not in [100.0,2000.0]",
        "LogVerse: [DUCKY-TEST] PASS summary: 1 passed, 1 failed",
    ]
    parsed = parse_test_results(lines)
    assert parsed["passed"] == 1
    assert parsed["failed"] == 1
    assert parsed["ok"] is False
    names = {r["name"] for r in parsed["results"]}
    assert "leveling.xp_per_level" in names
    assert "movement.walk_speed" in names


def test_save_and_load_simulation_scenario(tmp_path: Path):
    saved = save_simulation_scenario(
        str(tmp_path),
        "button grants",
        "StartButton",
        expect_effects=["grant_item"],
    )
    assert saved["ok"]
    loaded = load_simulation_scenario(str(tmp_path), "button_grants")
    assert loaded["ok"]
    assert loaded["scenario"]["device"] == "StartButton"
    assert loaded["scenario"]["expect_effects"] == ["grant_item"]


def test_compare_simulation_effects():
    sim = simulate_device_event(_fixture_snapshot(), "StartButton", "InteractedWithEvent")
    ok = compare_simulation_effects(sim, ["grant_item"])
    assert ok["ok"] is True
    bad = compare_simulation_effects(sim, ["teleport"])
    assert bad["ok"] is False
    assert "teleport" in bad["missing"]


def test_add_verse_test_case(tmp_path: Path):
    result = add_verse_test_case(
        str(tmp_path),
        "leveling.level3_xp",
        kind="equal",
        actual="Xp",
        expected="300",
        setup_line="Xp := 300",
    )
    assert result["ok"] and result["added"]
    text = (tmp_path / "Verse/DuckyTests/ducky_test_device.verse").read_text(encoding="utf-8")
    assert "ExpectEqual(\"leveling.level3_xp\", Xp, 300)" in text
    assert "Xp := 300" in text
    again = add_verse_test_case(str(tmp_path), "leveling.level3_xp", kind="equal", actual="Xp", expected="300")
    assert again["added"] is False
