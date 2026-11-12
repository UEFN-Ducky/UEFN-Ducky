"""Epic answers an unknown toolset/tool with text, not an error: the pool must raise on it."""

from __future__ import annotations

from backend.mcp_plugins import epic_verse_files
from backend.mcp_plugins.client_pool import epic_not_found, epic_not_found_hint
from backend.tools.tester import session_play

# The reply a Codex scene capture got on Nov 12 2026 (feedback card fbd9x4p7m2q6w), cut as it was.
_SCENE_REPLY = (
    "Toolset 'editor_toolset.toolsets.scene.SceneTools' not found. Available toolsets: "
    "ToolsetRegistry.AgentSkillToolset, AIAssistantEditor.AIAssistantToolset, ConsoleToolset.LogsToolset, "
    "EditorToolset.ActorToolset, EditorToolset.AssetToolset, EditorToolset.EditorAppToolset, "
    "EditorToolset.LevelToolset, EditorToolset.StaticMeshToolset, PCGToolset.PCGToolset, PhysicsToolsets.Physic"
)


def test_an_old_toolset_name_gets_the_43_00_name_and_the_capture_tool() -> None:
    hint = epic_not_found_hint(_SCENE_REPLY)
    assert "Renamed in 43.00: use EditorToolset.LevelToolset" in hint
    assert "take_high_res_screenshot" in hint and "\n" not in hint
    assert "ValkyrieFortniteToolset.SessionToolset" in epic_not_found_hint(
        "Toolset 'ValkyrieToolset.SessionToolset' not found. Available toolsets: ValkyrieFortniteToolset.SessionToolset")


def test_an_unknown_toolset_gets_the_listed_ones_about_the_same_thing() -> None:
    reply = ("Toolset 'NiagaraTools' not found. Available toolsets: EditorToolset.ActorToolset, "
             "NiagaraToolsets.NiagaraToolset_Info, NiagaraToolsets.NiagaraToolset_System, PCGToolset.PCGToolset")
    assert epic_not_found_hint(reply) == (
        "Closest available: NiagaraToolsets.NiagaraToolset_Info, NiagaraToolsets.NiagaraToolset_System.")


def test_no_hint_when_nothing_is_close_or_it_is_a_tool_that_is_missing() -> None:
    assert epic_not_found_hint("Toolset 'Zzz.Qqq' not found. Available toolsets: EditorToolset.ActorToolset") == ""
    assert epic_not_found_hint("Error: Tool 'StartGame' not found in toolset X") == ""
    assert epic_not_found_hint("Unknown tool NoSuchTool") == ""


def test_not_found_replies_are_failures() -> None:
    assert epic_not_found("Toolset 'ValkyrieToolset.SessionToolset' not found")
    assert epic_not_found("Error: Tool 'StartGame' not found in toolset X")
    assert epic_not_found("Unknown tool NoSuchTool")  # live reply for a bad tool name


def test_normal_replies_pass() -> None:
    assert not epic_not_found('{"returnValue": []}')
    assert not epic_not_found("Session started")
    assert not epic_not_found("")
    # A log or file that mentions the phrase is data, not a failed call.
    assert not epic_not_found('{"returnValue": ["LogMCP: Toolset \'X\' not found"]}')
    assert not epic_not_found("line 1\nUnknown tool Foo was logged")


def test_callers_use_43_00_toolset_names() -> None:
    # 43.00 renamed these; the old ValkyrieToolset.* names answer "not found".
    assert session_play._SESSION == "ValkyrieFortniteToolset.SessionToolset"
    assert session_play._DEVICE == "ValkyrieFortniteToolset.DeviceToolset"
    assert epic_verse_files.VERSE_TOOLSET == "VerseToolset.VerseToolset"
