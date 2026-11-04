"""Agents are told when a plugin's panel is off screen (its panel code is paused)."""

from __future__ import annotations

import time
from types import SimpleNamespace

import pytest

from backend.uefn_plugins import panel_presence as pp


@pytest.fixture
def docs(monkeypatch):
    store: dict[str, object] = {}
    fake = SimpleNamespace(
        get_doc=lambda table, key: store.get(key),
        set_doc=lambda table, key, value: store.__setitem__(key, value),
        delete_doc=lambda table, key: store.pop(key, None),
        list_docs=lambda table, prefix="": {k: v for k, v in store.items() if k.startswith(prefix)},
    )
    monkeypatch.setattr("backend.store.repos.kv", fake, raising=False)
    import backend.store.repos as repos

    monkeypatch.setattr(repos, "kv", fake, raising=False)
    monkeypatch.setattr(pp, "_has_panels", lambda pid: ["main"] if pid == "imk" else [])
    monkeypatch.setattr(pp, "_label", lambda pid: "Island Media Kit")
    pp._NOTED.clear()
    return store


def _as_agent(monkeypatch, chat: str) -> None:
    monkeypatch.setattr("backend.workspace.identity.resolve_context", lambda: SimpleNamespace(conv_id=chat))


def test_state_follows_the_window(docs) -> None:
    assert pp.panel_state("imk")["open"] is False
    pp.note_panel("main", "plugin:imk:main", visible=True)
    assert pp.panel_state("imk") | {"since": 0} == {"open": True, "visible": True, "since": 0, "closed": 0.0,
                                                    "panels": ["main"]}
    pp.note_panel("main", "plugin:imk:main", visible=False)
    assert pp.panel_state("imk")["visible"] is False and pp.panel_state("imk")["open"] is True
    before = time.time()
    pp.note_panel("main", "plugin:imk:main", visible=False, open_=False)
    assert pp.panel_state("imk")["open"] is False
    closed = pp.panel_state("imk")["closed"]
    assert closed >= before  # when its tab closed: the plugin's auto locks go soon after
    pp.note_panel("main", "plugin:imk:main", visible=False, open_=False)  # closed twice: the first time stays
    assert pp.panel_state("imk")["closed"] == closed
    pp.note_panel("main", "plugin:imk:main", visible=True)
    pp.forget_all()
    assert pp.panel_state("imk")["open"] is False


def test_an_agent_is_told_once_while_the_panel_is_off_screen(docs, monkeypatch) -> None:
    _as_agent(monkeypatch, "lead")
    note = pp.panel_note_once("imk")
    assert "Island Media Kit panel is not open" in note
    assert 'ducky_ui_navigate("plugin_panel", "imk:main")' in note
    assert pp.panel_note_once("imk") == ""  # once per chat
    _as_agent(monkeypatch, "builder")
    assert pp.panel_note_once("imk") != ""  # another agent hears it too
    pp.note_panel("main", "plugin:imk:main", visible=True)
    assert pp.panel_note_once("imk") == ""  # on screen: nothing to say
    pp.note_panel("main", "plugin:imk:main", visible=False)
    assert "is open on a tab that is not showing" in pp.panel_note_once("imk")  # hidden again: said again


def test_no_note_for_people_or_plugins_without_panels(docs, monkeypatch) -> None:
    monkeypatch.setattr("backend.workspace.identity.resolve_context", lambda: None)
    assert pp.panel_note_once("imk") == ""
    _as_agent(monkeypatch, "lead")
    assert pp.panel_note_once("no-panels") == ""


def test_tool_results_carry_the_note(monkeypatch) -> None:
    from backend.uefn_plugins.plugin_host import panel_rpc

    monkeypatch.setattr(pp, "panel_note_once", lambda pid: "The panel is off screen.")
    assert panel_rpc._with_panel_note("imk", {"ok": True}) == {"ok": True, "panel_note": "The panel is off screen."}
    assert panel_rpc._with_panel_note("imk", "done").endswith("\n\nThe panel is off screen.")
    monkeypatch.setattr(pp, "panel_note_once", lambda pid: "")
    assert panel_rpc._with_panel_note("imk", {"ok": True}) == {"ok": True}


def test_agents_can_open_a_plugin_panel(monkeypatch) -> None:
    from backend.tools.panel import panel_ui

    calls = []
    monkeypatch.setattr(panel_ui, "panel_rpc", lambda method, params, **kw: calls.append((method, params)) or {"ok": True, "tab": "plugin:imk:main"})
    from backend.uefn_plugins.plugin_host.testing import patch_host

    patch_host(monkeypatch, "get_contributions",
               lambda: {"ui_panels": [{"plugin_id": "imk", "id": "main", "title": "Island Media Kit"}]})
    assert panel_ui._navigate("plugin_panel", "imk") == {"ok": True, "route": "plugin_panel", "tab": "plugin:imk:main"}
    assert calls == [("open_plugin_panel", {"plugin_id": "imk", "panel_id": "main", "title": "Island Media Kit"})]
    assert "error" in panel_ui._navigate("plugin_panel", "nothing")


def test_plugins_get_a_seekable_link_for_local_media(tmp_path) -> None:
    from backend.uefn_plugins.plugin_host.api import _PluginApi

    proxy = tmp_path / "proxy_720p.mp4"
    proxy.write_bytes(b"\x00")
    api = _PluginApi.__new__(_PluginApi)
    url = api.media_url(str(proxy))
    assert url.startswith("http://127.0.0.1:") and "/workflow-media/" in url and url.endswith("/proxy_720p.mp4")
    assert api.media_url(str(tmp_path / "clip.avi")) == ""


def test_plugins_get_a_plain_local_folder_for_rebuildable_files(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("backend.skills.discovery.appdata_dir", lambda: tmp_path)
    folder = pp.plugin_local_dir("island-media-kit", "proxies")
    assert folder == tmp_path / "plugin_local" / "island-media-kit" / "proxies" and folder.is_dir()
    assert pp.plugin_local_dir("island-media-kit", "../../etc") == tmp_path / "plugin_local" / "island-media-kit" / "etc"
    (folder / "clip_720p.mp4").write_bytes(b"\x00")
    pp.erase_plugin_local("island-media-kit")
    assert not (tmp_path / "plugin_local" / "island-media-kit").exists()
