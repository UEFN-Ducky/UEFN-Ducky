"""What plugin and nested MCP tools make is kept in Storage under the plugin, in Local or the
team, with no plugin code: found in the tool's answer, only files the call made, never twice."""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="DPAPI")

from backend.store.repos import plugin_data as repo
from backend.uefn_plugins import auto_storage, general_storage as gs
from backend.uefn_plugins import test_scopes
from backend.uefn_plugins.test_scopes import FakeSpaces, open_space

account_keys = test_scopes.account_keys
who = test_scopes.who
fake_spaces = test_scopes.fake_spaces


@pytest.fixture(autouse=True)
def _fresh(monkeypatch) -> None:
    auto_storage._RECENT.clear()
    monkeypatch.setattr(auto_storage, "enabled", lambda: True)
    # Run the background copy inline so the test sees it.
    monkeypatch.setattr(auto_storage, "_spawn", lambda o, l, t, r, s: auto_storage.keep_outputs(o, l, t, r, s))


def _old(path: Path) -> None:
    past = time.time() - 3600
    os.utime(path, (past, past))


def test_paths_are_found_in_json_at_any_depth_and_in_text(tmp_path: Path) -> None:
    png, mov, log = tmp_path / "frame.png", tmp_path / "My Cut" / "edit.mov", tmp_path / "run.log"
    answer = json.dumps({"ok": True, "result": {"files": [{"path": str(png)}], "note": "x"}, "log": str(log)})
    assert auto_storage.candidate_paths(answer) == [png]
    text = f"Rendered to {mov} and wrote {tmp_path / 'job.jsx'}."
    assert auto_storage.candidate_paths(text) == [mov]
    assert auto_storage.candidate_paths({"path": "relative/frame.png"}) == []


def test_a_tool_render_is_kept_under_the_plugin_folder(who, tmp_path: Path) -> None:
    who.be("ana@x.org")
    since = time.time()
    frame = tmp_path / "Title Frame.png"
    frame.write_bytes(b"PNG-A")
    before = tmp_path / "input.psd"
    before.write_bytes(b"PSD")
    _old(before)  # an input the tool only read
    kept = auto_storage.keep_outputs("adobe", "Adobe", "aftereffects_render_frame",
                                     {"ok": True, "path": str(frame), "source": str(before)}, since)
    assert len(kept) == 1
    item = kept[0]
    day = time.strftime("%Y-%m-%d")
    assert (item["name"], item["folder"], item["by"]) == ("Title Frame.png", f"adobe/{day}", "adobe")
    assert (item["tool"], item["source_path"]) == ("aftereffects_render_frame", str(frame))
    assert gs.get(item["id"], with_data=True)["data"] == b"PNG-A"
    assert [r["name"] for r in gs.items("adobe")] == ["Title Frame.png"]


def test_the_same_file_is_kept_once_and_made_again_it_replaces_the_one_kept(who, tmp_path: Path) -> None:
    who.be("ana@x.org")
    since = time.time()
    out = tmp_path / "render.png"
    out.write_bytes(b"ONE")
    answer = {"path": str(out)}
    assert len(auto_storage.keep_outputs("blender", "Blender", "render", answer, since)) == 1
    assert auto_storage.keep_outputs("blender", "Blender", "render", answer, since) == []  # named again
    auto_storage._RECENT.clear()
    assert auto_storage.keep_outputs("blender", "Blender", "render", answer, since) == []  # same bytes
    out.write_bytes(b"TWO")
    second = auto_storage.keep_outputs("blender", "Blender", "render", answer, time.time())
    # One item per thing made, not one per try: a team gets one change per item.
    assert [r["name"] for r in second] == ["render.png"]
    assert [r["name"] for r in gs.items("blender")] == ["render.png"]
    assert gs.get(second[0]["id"], with_data=True)["data"] == b"TWO"
    out.write_bytes(b"THREE")
    assert len(auto_storage.keep_outputs("blender", "Blender", "render", answer, time.time())) == 1
    assert [r["name"] for r in gs.items("blender")] == ["render.png"]


def test_it_lands_in_the_team_copy(who, fake_spaces: FakeSpaces, tmp_path: Path) -> None:
    aid = open_space(fake_spaces, who, "ana@x.org", "spaceT", "Alpha", plugin=gs.GENERAL)
    since = time.time()
    clip = tmp_path / "trailer.mp4"
    clip.write_bytes(b"MP4")
    kept = auto_storage.keep_outputs("adobe", "Adobe", "premiere_export_sequence", {"path": str(clip)}, since)
    assert len(kept) == 1
    assert repo.count_dirty(aid, "spaceT", gs.GENERAL) == 2  # its file and doc, queued for the team


def test_plugin_tools_and_nested_servers_both_route_here(who, monkeypatch, tmp_path: Path) -> None:
    who.be("ana@x.org")
    from backend.mcp_plugins import store as mcp_store
    from backend.uefn_plugins import store as plugin_store

    monkeypatch.setattr(plugin_store, "load_plugin_manifest", lambda pid: {"id": pid, "label": "Adobe"})
    monkeypatch.setattr(mcp_store, "load_plugin_manifest", lambda sid: {"id": sid, "label": "Blender MCP"})
    since = time.time()
    a, b = tmp_path / "logo.svg", tmp_path / "scene.glb"
    a.write_bytes(b"<svg/>")
    b.write_bytes(b"glTF")
    auto_storage.after_plugin_tool("adobe", "illustrator_export", json.dumps({"output_path": str(a)}), since)
    auto_storage.after_nested_tool("blender", "export_glb", f"Exported {b}", since)
    day = time.strftime("%Y-%m-%d")
    assert [r["name"] for r in gs.items(f"adobe/{day}")] == ["logo.svg"]
    assert [r["name"] for r in gs.items(f"blender-mcp/{day}")] == ["scene.glb"]


def test_a_plugin_or_the_person_can_turn_it_off(who, monkeypatch, tmp_path: Path) -> None:
    who.be("ana@x.org")
    from backend.uefn_plugins import store as plugin_store

    out = tmp_path / "x.png"
    out.write_bytes(b"X")
    monkeypatch.setattr(plugin_store, "load_plugin_manifest", lambda pid: {"label": "Quiet", "keep_outputs": False})
    auto_storage.after_plugin_tool("quiet", "make", {"path": str(out)}, time.time() - 1)
    monkeypatch.setattr(plugin_store, "load_plugin_manifest", lambda pid: {"label": "Loud"})
    monkeypatch.setattr(auto_storage, "enabled", lambda: False)
    auto_storage.after_plugin_tool("loud", "make", {"path": str(out)}, time.time() - 1)
    assert gs.items() == []


def test_the_plugin_tool_wrapper_calls_it(monkeypatch) -> None:
    """Every api.tool goes through the hook, with the time the call started."""
    src = (Path(__file__).parent / "plugin_host" / "api.py").read_text(encoding="utf-8")
    assert "after_plugin_tool(pid, tool_name, result, started)" in src
    pool = (Path(__file__).parents[1] / "mcp_plugins" / "client_pool.py").read_text(encoding="utf-8")
    assert "after_nested_tool(plugin_id, original_name, text, started)" in pool
