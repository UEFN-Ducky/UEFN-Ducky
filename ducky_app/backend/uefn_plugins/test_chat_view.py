"""A tool's card in chat: the plugin it belongs to, links for the media it made, and
ducky_ui_navigate leaving an Open button instead of switching the person's tab."""

from __future__ import annotations

import json

from backend.uefn_plugins import chat_view


def test_bare_tool_names() -> None:
    assert chat_view.bare_tool_name("mcp__uefn__imk_render_get") == "imk_render_get"
    assert chat_view.bare_tool_name("uefn.imk_render_get") == "imk_render_get"
    assert chat_view.bare_tool_name("imk_render_get") == "imk_render_get"


def test_links_only_for_media_files_that_exist(tmp_path) -> None:
    video = tmp_path / "Trailer 2.mp4"
    video.write_bytes(b"\x00")
    (tmp_path / "run.exe").write_bytes(b"MZ")
    link = chat_view.media_link(str(video))
    assert link and link["kind"] == "video" and link["name"] == "Trailer 2.mp4"
    assert "/workflow-media/" in link["url"] and link["url"].startswith("http://127.0.0.1:")
    assert chat_view.media_link(str(tmp_path / "run.exe")) is None  # not media
    assert chat_view.media_link(str(tmp_path / "gone.png")) is None  # not there
    assert chat_view.media_link("Trailer 2.mp4") is None  # not a full path
    out = chat_view.chat_tool_view([], [str(video), str(video), str(tmp_path / "gone.png")])
    assert [m["asked"] for m in out["media"]] == [str(video)]


def test_owner_names_the_plugin_and_its_panel(monkeypatch) -> None:
    monkeypatch.setattr("backend.uefn_plugins.host.plugin_for_tool",
                        lambda name: "island-media-kit" if name.startswith("imk_") else "ducky")
    monkeypatch.setattr("backend.uefn_plugins.host.get_ui_contributions",
                        lambda: {"ui_panels": [{"plugin_id": "island-media-kit", "id": "main", "title": "Media Kit"}]})
    monkeypatch.setattr("backend.uefn_plugins.store.load_plugin_manifest",
                        lambda pid: {"label": "Island Media Kit"})
    out = chat_view.chat_tool_view(["mcp__uefn__imk_render_get", "workspace_read_file"], [])
    assert out["tools"]["mcp__uefn__imk_render_get"] == {
        "plugin_id": "island-media-kit", "label": "Island Media Kit", "panel_id": "main", "panel_title": "Media Kit",
    }
    assert out["tools"]["workspace_read_file"] == {}


def test_navigate_leaves_an_open_button_and_moves_nothing(monkeypatch) -> None:
    from backend.tools.panel import panel_ui

    calls = []
    monkeypatch.setattr(panel_ui, "panel_rpc", lambda method, params, **kw: calls.append(method) or {"ok": True})
    monkeypatch.setattr("backend.uefn_plugins.host.get_contributions",
                        lambda: {"ui_panels": [{"plugin_id": "imk", "id": "main", "title": "Island Media Kit"}]})
    out = json.loads(panel_ui.ducky_ui_navigate("plugin_panel", "imk"))
    assert out["in_chat"] is True and out["item_id"] == "imk:main" and out["title"] == "Island Media Kit"
    out = json.loads(panel_ui.ducky_ui_navigate("workflows"))
    assert out["ok"] is True and out["route"] == "workflows"
    assert calls == []  # nothing pulled to the front
    assert "error" in json.loads(panel_ui.ducky_ui_navigate("plugin_panel", "nothing"))
    assert "error" in json.loads(panel_ui.ducky_ui_navigate("nowhere"))
