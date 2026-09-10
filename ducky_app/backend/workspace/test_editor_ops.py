"""The editor-op classifier: complete, explicit, and biased towards recording."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from backend.workspace import editor_ops as ops
from backend.workspace.editor_ops import (
    EDITOR_OPS,
    MUT_OPAQUE,
    MUT_READ,
    MUT_WRITE,
    READ_COMMANDS,
    REVERT_AUTO,
    REVERT_MANUAL,
    REVERT_NONE,
    classify,
    classify_plugin_tool,
    downgrade,
    is_mutation,
    slot_path,
)

LISTENER = Path(__file__).resolve().parents[2] / "uefn_listener" / "listener"
_REGISTER = re.compile(r'register\(\s*"([a-z0-9_]+)"\s*\)')


def registered_commands() -> set[str]:
    """Every command name the listener registers, read from its source.

    The listener tree cannot be imported outside UEFN (it imports ``unreal``),
    so this reads the source instead.
    """
    found: set[str] = set()
    for path in LISTENER.rglob("*.py"):
        found.update(_REGISTER.findall(path.read_text(encoding="utf-8", errors="replace")))
    return found


# --- completeness ----------------------------------------------------------------


def test_the_listener_tree_is_readable() -> None:
    # Guards the test below: a silent zero would make it vacuously pass.
    assert len(registered_commands()) > 150


def test_every_registered_command_is_classified() -> None:
    unclassified = sorted(registered_commands() - set(EDITOR_OPS) - READ_COMMANDS)
    assert not unclassified, (
        "These listener commands are in neither EDITOR_OPS nor READ_COMMANDS, so they would be "
        "recorded as opaque with no inverse. Classify them in backend/workspace/editor_ops.py:\n  "
        + "\n  ".join(unclassified)
    )


def test_tables_are_disjoint() -> None:
    assert not (set(EDITOR_OPS) & READ_COMMANDS)


def heavy_commands() -> set[str]:
    """``tick.py``'s throttle set — includes commands Store plugins register."""
    text = (LISTENER / "tick.py").read_text(encoding="utf-8")
    after = text.split("_HEAVY_COMMANDS", 1)[1]
    start = after.index("{")
    depth = 0
    for i, ch in enumerate(after[start:], start):
        depth += (ch == "{") - (ch == "}")
        if depth == 0:
            return set(re.findall(r'"([a-z0-9_]+)"', after[start : i + 1]))
    raise AssertionError("unbalanced braces in _HEAVY_COMMANDS")


def test_no_stale_entries() -> None:
    """Every classified command exists somewhere in the codebase, so typos fail here.

    Store desktop plugins register their own listener handlers (animation/IK,
    materials), so a classified command need not be in the core listener tree —
    but it must at least be named in the throttle set.
    """
    known = registered_commands() | heavy_commands()
    stale = sorted(c for c in EDITOR_OPS if c not in known)
    assert not stale, f"classified but named nowhere in the listener: {stale}"


def test_heavy_command_set_is_readable() -> None:
    heavy = heavy_commands()
    assert "spawn_actor" in heavy and len(heavy) > 80


# --- classification --------------------------------------------------------------


@pytest.mark.parametrize("command", ["search_assets", "list_assets", "get_all_actors"])
def test_heavy_reads_are_reads(command: str) -> None:
    """_HEAVY_COMMANDS is a throttle list; these three are reads that live in it."""
    assert classify(command).mutates == MUT_READ
    assert not is_mutation(command)


@pytest.mark.parametrize(
    ("command", "expected"),
    [
        ("spawn_actor", MUT_WRITE),
        ("set_actor_transform", MUT_WRITE),
        ("execute_python", MUT_OPAQUE),
        ("exec_console_command", MUT_OPAQUE),
        ("get_level_info", MUT_READ),
        ("save_current_level", MUT_READ),
    ],
)
def test_known_commands(command: str, expected: str) -> None:
    assert classify(command).mutates == expected


def test_unknown_commands_are_opaque_not_read() -> None:
    spec = classify("some_new_plugin_command")
    assert spec.mutates == MUT_OPAQUE
    assert spec.revertable == REVERT_MANUAL
    assert "plugin command" in spec.note
    assert is_mutation("some_new_plugin_command")
    assert classify("").mutates == MUT_OPAQUE


def test_unknown_plugin_reads_are_reads_by_name() -> None:
    """Store-plugin list_/get_/*_capabilities must not land on a remove-by-hand list."""
    for command in ("list_npc_spawners", "get_anim_preset_info", "npc_author_capabilities"):
        assert classify(command).mutates == MUT_READ, command
        assert not is_mutation(command)
    assert classify("set_npc_spawner_definition").mutates == MUT_OPAQUE


def test_create_folder_is_nothing_to_undo() -> None:
    assert classify("create_folder").revertable == REVERT_NONE


def test_refused_deletes_are_recorded_but_never_revertable() -> None:
    for command in ("delete_actors", "delete_asset", "delete_directory"):
        spec = classify(command)
        assert spec.mutates == MUT_WRITE
        assert spec.revertable == REVERT_NONE
        assert "refused" in spec.note


def test_creations_are_marked_and_share_the_exists_facet() -> None:
    creators = [s for s in EDITOR_OPS.values() if s.creates]
    assert {"spawn_actor", "duplicate_asset", "create_data_table"} <= {s.command for s in creators}
    for spec in creators:
        if spec.mutates == MUT_OPAQUE:
            # An opaque command may create things, but it is an event rather than a
            # target: its slot is per-call, so it shares no facet with anything.
            assert spec.slot == "opaque", f"{spec.command} is opaque but its slot is {spec.slot!r}"
            continue
        assert spec.slot in ("exists", ""), f"{spec.command} creates but its slot is {spec.slot!r}"


def test_lossy_replacements_are_never_auto() -> None:
    for command in ("fill_data_table_from_json", "fill_data_table_from_csv",
                    "resize_verse_array_field"):
        assert classify(command).revertable != REVERT_AUTO
        assert classify(command).note


# --- nested MCP tools ------------------------------------------------------------


class Ann:
    def __init__(self, read_only=None, destructive=None):
        if read_only is not None:
            self.readOnlyHint = read_only
        if destructive is not None:
            self.destructiveHint = destructive


def test_plugin_tool_prefers_annotations() -> None:
    assert classify_plugin_tool("unreal__x", Ann(read_only=True)).mutates == MUT_READ
    destructive = classify_plugin_tool("unreal__x", Ann(destructive=True))
    assert destructive.mutates == MUT_OPAQUE and destructive.revertable == REVERT_NONE
    declared = classify_plugin_tool("unreal__x", Ann(read_only=False))
    assert declared.mutates == MUT_WRITE


def test_plugin_tool_falls_back_to_the_name() -> None:
    assert classify_plugin_tool("unreal__get_actors", None).mutates == MUT_READ
    assert classify_plugin_tool("unreal__list_devices", None).mutates == MUT_READ
    assert classify_plugin_tool("unreal__spawn_device", None).mutates == MUT_OPAQUE
    # An unprefixed write must not be mistaken for a read.
    assert classify_plugin_tool("unreal__set_property", None).mutates == MUT_OPAQUE


def test_plugin_tool_strips_the_namespace() -> None:
    assert classify_plugin_tool("unreal__get_all_actors", None).command == "get_all_actors"


# --- slots and downgrade ---------------------------------------------------------


def test_slot_path_is_stable_per_target_and_facet() -> None:
    a = slot_path("actor", "8F2A-1", "transform")
    assert a == "uefn://actor/8F2A-1/transform"
    assert slot_path("actor", "8F2A-1", "transform") == a
    assert slot_path("actor", "8F2A-1", "label") != a
    assert slot_path("actor", "8F2A-2", "transform") != a


def test_slot_path_tolerates_messy_identifiers() -> None:
    assert slot_path("asset", "/Game/Foo/Bar", "exists") == "uefn://asset/Game/Foo/Bar/exists"
    assert slot_path("actor", "", "transform") == "uefn://actor/unknown/transform"
    assert slot_path("actor", "x", "") == "uefn://actor/x"
    assert slot_path("object", "Cube", "mesh", program="blender") == "blender://object/Cube/mesh"


def test_downgrade_never_raises_revertability() -> None:
    spec = classify("set_actor_transform")
    assert spec.revertable == REVERT_AUTO
    assert downgrade(spec, REVERT_MANUAL, "old target is gone").revertable == REVERT_MANUAL
    manual = downgrade(spec, REVERT_MANUAL)
    assert downgrade(manual, REVERT_AUTO).revertable == REVERT_MANUAL
    assert downgrade(spec, REVERT_NONE).revertable == REVERT_NONE


def test_specs_are_frozen() -> None:
    with pytest.raises(Exception):
        classify("spawn_actor").revertable = REVERT_NONE  # type: ignore[misc]


def test_module_exposes_no_mutable_shared_state() -> None:
    # EDITOR_OPS is a dict by design; make sure a caller mutating it cannot be
    # confused for a classification change elsewhere in the process.
    assert isinstance(ops.READ_COMMANDS, frozenset)


def test_npc_definition_info_is_a_read() -> None:
    spec = classify("get_npc_definition_info")
    assert spec.mutates == MUT_READ
    assert classify("list_npc_definitions").mutates == MUT_READ

