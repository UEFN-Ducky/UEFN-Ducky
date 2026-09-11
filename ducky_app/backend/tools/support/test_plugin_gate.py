"""Core Store heal + host-disk tools stay callable without a Store click."""

from backend.tools.support.plugin_gate import HOST_DISK_TOOLS, heal_enable_store_plugin


def test_host_disk_tools_include_verse_file_and_skill_reader() -> None:
    assert "skill_read_subskill" in HOST_DISK_TOOLS
    assert "search_verse_digest" in HOST_DISK_TOOLS
    assert "workspace_list_verse_errors" in HOST_DISK_TOOLS


def test_heal_skips_unknown_and_non_core_ids() -> None:
    assert heal_enable_store_plugin("not-a-plugin") is False
    assert heal_enable_store_plugin("blender") is False
