"""Nested Epic unreal-mcp catalog, fast-fail, prefix routing, pruned Ducky tools."""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from unittest.mock import patch

from backend.mcp_plugins import epic, store
from backend.mcp_plugins.client_pool import PluginClientPool
from backend.mcp_plugins.epic import EPIC_MCP_DEFAULT_URL, EPIC_MCP_SERVER_ID, EPIC_PRUNED_DUCKY_TOOLS
from backend.mcp_plugins.registry import namespace_tool_name, parse_plugin_tool, rebuild_plugin_prefix_cache

import pytest


@pytest.fixture(autouse=True)
def _an_editor_runs(monkeypatch) -> None:
    """These tests fake the editor's port; its process "runs" too, so the presence gate
    (no connect while no editor runs, test_editor_presence) lets the probe through."""
    from backend.bridge import editor_presence

    monkeypatch.setattr(editor_presence, "editor_running", lambda: True)
    monkeypatch.setattr(editor_presence, "uefn_running", lambda: True)


def test_catalog_plugin_json_is_http_unreal_mcp() -> None:
    path = (
        Path(__file__).resolve().parents[2]
        / "frontend"
        / "mcp_plugins"
        / "unreal-mcp"
        / "plugin.json"
    )
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["id"] == EPIC_MCP_SERVER_ID
    assert data["kind"] == "catalog"
    assert data["default_enabled"] is True
    assert data["tool_prefix"] == "unreal"
    assert data["server"]["type"] == "http"
    assert data["server"]["url"] == EPIC_MCP_DEFAULT_URL
    assert data["setup_steps"]


def test_catalog_seeds_mcp_json(tmp_path: Path, monkeypatch) -> None:
    store.appdata_dir().mkdir(parents=True, exist_ok=True)
    bundled = Path(__file__).resolve().parents[2] / "frontend" / "mcp_plugins"
    monkeypatch.setattr(store, "bundled_mcp_plugins_dir", lambda: bundled)

    class _Settings:
        enabled_mcp_plugins: list[str] = []

        def save(self) -> None:
            pass

        @classmethod
        def load(cls) -> "_Settings":
            return cls()

    with (
        patch("frontend.settings.PanelSettings", _Settings),
        patch("backend.mcp_plugins.client_pool.get_plugin_pool") as pool,
    ):
        pool.return_value.invalidate_tools_cache = lambda: None
        store.ensure_mcp_config()
        cfg = store.load_mcp_config()
        block = cfg["mcpServers"][EPIC_MCP_SERVER_ID]
        assert block.get("type") == "http"
        assert block.get("url") == EPIC_MCP_DEFAULT_URL
        assert block.get("disabled") is not True
        assert EPIC_MCP_SERVER_ID in store.get_enabled_plugin_ids()
        manifest = store.load_plugin_manifest(EPIC_MCP_SERVER_ID)
        assert manifest is not None
        assert manifest["tool_prefix"] == "unreal"
        assert manifest["kind"] == "catalog"


def test_http_transport_timeouts_cover_long_editor_calls() -> None:
    from backend.mcp_plugins.client_pool import _TOOL_TIMEOUT_SEC, _http_transport_kwargs

    kw = _http_transport_kwargs({"headers": {"X": "1"}})
    assert kw["timeout"] == _TOOL_TIMEOUT_SEC
    assert kw["timeout"] >= 180
    assert kw["sse_read_timeout"] >= 180
    assert kw["headers"] == {"X": "1"}


def test_http_down_editor_list_tools_fails_fast(monkeypatch) -> None:
    monkeypatch.setattr("backend.mcp_plugins.epic.tcp_probe_url", lambda *_a, **_k: False)
    pool = PluginClientPool()
    manifest = {
        "id": EPIC_MCP_SERVER_ID,
        "tool_prefix": "unreal",
        "server": {"type": "http", "url": EPIC_MCP_DEFAULT_URL},
    }
    monkeypatch.setattr(
        "backend.mcp_plugins.client_pool.load_plugin_manifest",
        lambda _pid: manifest,
    )
    monkeypatch.setattr(
        "backend.mcp_plugins.client_pool.resolve_server_block",
        lambda _m: {"type": "http", "url": EPIC_MCP_DEFAULT_URL, "headers": {}},
    )
    t0 = time.perf_counter()
    try:
        asyncio.run(pool.list_tools_for_plugin(EPIC_MCP_SERVER_ID))
        raise AssertionError("expected ConnectionError")
    except ConnectionError as exc:
        assert "unreachable" in str(exc).lower()
    assert time.perf_counter() - t0 < 3.0


def test_unreal_prefix_routes_to_plugin_id() -> None:
    manifest = {"id": EPIC_MCP_SERVER_ID, "tool_prefix": "unreal"}
    rebuild_plugin_prefix_cache([manifest])
    assert namespace_tool_name(manifest, "compile_verse") == "unreal__compile_verse"
    assert parse_plugin_tool("unreal__compile_verse") == (EPIC_MCP_SERVER_ID, "compile_verse")


def test_pruned_ducky_tools_are_not_registered() -> None:
    from backend.tools.scene import scene_graph
    from backend.tools.uefn import device_focused, editor

    leaked = EPIC_PRUNED_DUCKY_TOOLS & (
        set(dir(scene_graph)) | set(dir(editor)) | set(dir(device_focused))
    )
    assert not leaked, leaked
    assert hasattr(scene_graph, "instantiate_prefab")
    assert hasattr(scene_graph, "create_empty_prefab")


def test_tcp_probe_closed_port_is_false() -> None:
    assert epic.tcp_probe_url("http://127.0.0.1:1/mcp", timeout=0.2) is False


# --------------------------------------------------------------------------- UEFN vs Unreal Editor
# Both editors ship the same Unreal MCP on the same default port.


def _projects(tmp_path: Path) -> tuple[str, str, str]:
    island = tmp_path / "Island"
    island.mkdir()
    (island / "Island.uefnproject").write_text("{}", encoding="utf-8")
    game = tmp_path / "MyGame"
    (game / "Content").mkdir(parents=True)
    (game / "MyGame.uproject").write_text("{}", encoding="utf-8")
    repo = tmp_path / "repo"
    repo.mkdir()
    return str(island), str(game), str(repo)


def _probe(monkeypatch, *, process: str, root: str, up: bool = True) -> dict:
    monkeypatch.setattr(epic, "epic_mcp_url", lambda: EPIC_MCP_DEFAULT_URL)
    monkeypatch.setattr(epic, "epic_mcp_enabled", lambda: True)
    monkeypatch.setattr(epic, "tcp_probe_url", lambda *_a, **_k: up)
    monkeypatch.setattr(epic, "_listening_process", lambda _port: (process, 4242 if process else 0, 1.0))
    epic.invalidate_epic_mcp_probe()
    return epic.probe_epic_mcp(project_root=root)


def test_editor_of_process_names() -> None:
    assert epic.editor_of_process("UnrealEditorFortnite-Win64-Shipping.exe") == epic.EDITOR_UEFN
    assert epic.editor_of_process("UnrealEditor.exe") == epic.EDITOR_UNREAL
    assert epic.editor_of_process("UnrealEditor-Win64-DebugGame.exe") == epic.EDITOR_UNREAL
    assert epic.editor_of_process("python.exe") == "other"
    assert epic.editor_of_process("") == ""


def test_expected_editor_follows_the_project(tmp_path: Path) -> None:
    island, game, repo = _projects(tmp_path)
    assert epic.expected_editor(island) == epic.EDITOR_UEFN
    assert epic.expected_editor(game) == epic.EDITOR_UNREAL
    assert epic.expected_editor(repo) == ""
    assert epic.expected_editor("") == ""


def test_island_refuses_unreal_editor_on_the_port(tmp_path: Path, monkeypatch) -> None:
    island, _game, _repo = _projects(tmp_path)
    result = _probe(monkeypatch, process="UnrealEditor.exe", root=island)
    assert result["epic_mcp_online"] is False
    assert result["epic_mcp_reason"] == "other_editor"
    assert result["epic_mcp_editor"] == epic.EDITOR_UNREAL
    assert "held by Unreal Editor" in result["epic_mcp_setup_steps"][0]


def test_unreal_project_uses_unreal_editor_and_refuses_uefn(tmp_path: Path, monkeypatch) -> None:
    _island, game, _repo = _projects(tmp_path)
    ok = _probe(monkeypatch, process="UnrealEditor.exe", root=game)
    assert ok["epic_mcp_online"] is True
    assert ok["epic_mcp_editor"] == epic.EDITOR_UNREAL
    assert ok["epic_mcp_setup_steps"] == []
    blocked = _probe(monkeypatch, process="UnrealEditorFortnite-Win64-Shipping.exe", root=game)
    assert blocked["epic_mcp_reason"] == "other_editor"
    assert "held by UEFN" in blocked["epic_mcp_setup_steps"][0]


def test_plain_folder_takes_either_editor(tmp_path: Path, monkeypatch) -> None:
    _island, _game, repo = _projects(tmp_path)
    for process, editor in (("UnrealEditor.exe", "unreal"), ("UnrealEditorFortnite-Win64-Shipping.exe", "uefn")):
        result = _probe(monkeypatch, process=process, root=repo)
        assert result["epic_mcp_online"] is True
        assert result["epic_mcp_editor"] == editor


def test_other_program_on_the_port_is_port_taken(tmp_path: Path, monkeypatch) -> None:
    island, _game, _repo = _projects(tmp_path)
    result = _probe(monkeypatch, process="python.exe", root=island)
    assert result["epic_mcp_online"] is False
    assert result["epic_mcp_reason"] == "port_taken"
    assert "python.exe" in result["epic_mcp_setup_steps"][0]


def test_unknown_owner_stays_online(tmp_path: Path, monkeypatch) -> None:
    island, _game, _repo = _projects(tmp_path)
    result = _probe(monkeypatch, process="", root=island)
    assert result["epic_mcp_online"] is True
    assert result["epic_mcp_editor"] == ""


def test_offline_steps_match_the_editor(tmp_path: Path, monkeypatch) -> None:
    island, game, _repo = _projects(tmp_path)
    assert _probe(monkeypatch, process="", root=game, up=False)["epic_mcp_setup_steps"] == list(
        epic.UNREAL_MCP_SETUP_STEPS
    )
    assert _probe(monkeypatch, process="", root=island, up=False)["epic_mcp_setup_steps"] == list(
        epic.EPIC_MCP_SETUP_STEPS
    )


def test_port_owner_is_cached_until_the_port_closes(tmp_path: Path, monkeypatch) -> None:
    island, _game, _repo = _projects(tmp_path)
    calls: list[int] = []
    monkeypatch.setattr(epic, "epic_mcp_url", lambda: EPIC_MCP_DEFAULT_URL)
    monkeypatch.setattr(epic, "epic_mcp_enabled", lambda: True)
    monkeypatch.setattr(epic, "tcp_probe_url", lambda *_a, **_k: True)
    monkeypatch.setattr(epic, "_listening_process", lambda port: calls.append(port) or ("UnrealEditor.exe", 4242, 1.0))
    monkeypatch.setattr(epic, "_same_process", lambda pid, started: (pid, started) == (4242, 1.0))
    epic.invalidate_epic_mcp_probe()
    epic.probe_epic_mcp(project_root=island, ttl_sec=0)
    epic.probe_epic_mcp(project_root=island, ttl_sec=0)
    assert calls == [8000]
    monkeypatch.setattr(epic, "tcp_probe_url", lambda *_a, **_k: False)
    epic.probe_epic_mcp(project_root=island, ttl_sec=0)
    monkeypatch.setattr(epic, "tcp_probe_url", lambda *_a, **_k: True)
    epic.probe_epic_mcp(project_root=island, ttl_sec=0)
    assert calls == [8000, 8000]


def test_port_owner_is_checked_again_when_its_process_is_gone(tmp_path: Path, monkeypatch) -> None:
    # UEFN closes and Unreal Editor takes the port between two probes: the port never
    # looked closed, so only the cached process's identity tells them apart.
    island, _game, _repo = _projects(tmp_path)
    owners = iter([("UnrealEditorFortnite-Win64-Shipping.exe", 100, 1.0), ("UnrealEditor.exe", 200, 2.0)])
    alive = {100}
    monkeypatch.setattr(epic, "epic_mcp_url", lambda: EPIC_MCP_DEFAULT_URL)
    monkeypatch.setattr(epic, "epic_mcp_enabled", lambda: True)
    monkeypatch.setattr(epic, "tcp_probe_url", lambda *_a, **_k: True)
    monkeypatch.setattr(epic, "_listening_process", lambda _port: next(owners))
    monkeypatch.setattr(epic, "_same_process", lambda pid, started: pid in alive)
    epic.invalidate_epic_mcp_probe()
    assert epic.probe_epic_mcp(project_root=island, ttl_sec=0)["epic_mcp_online"] is True
    alive.clear()
    swapped = epic.probe_epic_mcp(project_root=island, ttl_sec=0)
    assert swapped["epic_mcp_reason"] == "other_editor"
    assert swapped["epic_mcp_editor"] == epic.EDITOR_UNREAL


def test_listening_process_names_a_real_listener() -> None:
    import os
    import socket

    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        sock.listen(1)
        name, pid, started = epic._listening_process(sock.getsockname()[1])
    assert pid == os.getpid() and name.lower().startswith("python") and started > 0
    assert epic._same_process(pid, started) is True
    assert epic._same_process(pid, started + 1) is False


def test_call_tool_refuses_the_wrong_editor_without_connecting(monkeypatch) -> None:
    rebuild_plugin_prefix_cache([{"id": EPIC_MCP_SERVER_ID, "tool_prefix": "unreal"}])
    monkeypatch.setattr("backend.mcp_plugins.client_pool.ensure_plugin_prefix_cache", lambda: None)
    monkeypatch.setattr(
        epic,
        "probe_epic_mcp",
        lambda **_k: {
            "epic_mcp_online": False,
            "epic_mcp_reason": "other_editor",
            "epic_mcp_editor": "unreal",
            "epic_mcp_setup_steps": ["Port 8000 is held by Unreal Editor, not UEFN."],
        },
    )
    pool = PluginClientPool()

    async def _no_connect(_pid):
        raise AssertionError("must not connect to the wrong editor")

    monkeypatch.setattr(pool, "_get_or_create", _no_connect)
    try:
        asyncio.run(pool.call_tool("unreal__list_toolsets", {}))
        raise AssertionError("expected RuntimeError")
    except RuntimeError as exc:
        assert "held by Unreal Editor" in str(exc)


def _probe_ports(monkeypatch, *, owners: dict[int, str], root: str) -> tuple[dict, list[int]]:
    """owners: port -> listening process; any other port is closed."""
    probed: list[int] = []
    monkeypatch.setattr(epic, "epic_mcp_url", lambda: EPIC_MCP_DEFAULT_URL)
    monkeypatch.setattr(epic, "epic_mcp_enabled", lambda: True)

    def _tcp(url, **_k):
        port = epic._host_port(url)[1]
        probed.append(port)
        return port in owners

    monkeypatch.setattr(epic, "tcp_probe_url", _tcp)
    monkeypatch.setattr(epic, "_listening_process", lambda port: (owners.get(port, ""), port, 1.0))
    epic.invalidate_epic_mcp_probe()
    return epic.probe_epic_mcp(project_root=root, ttl_sec=0), probed


def test_unreal_project_finds_unreal_editor_on_its_own_port(tmp_path: Path, monkeypatch) -> None:
    _island, game, _repo = _projects(tmp_path)
    owners = {8000: "UnrealEditorFortnite-Win64-Shipping.exe", 8001: "UnrealEditor.exe"}
    result, _ = _probe_ports(monkeypatch, owners=owners, root=game)
    assert result["epic_mcp_online"] is True
    assert result["epic_mcp_url"] == "http://127.0.0.1:8001/mcp"
    assert result["epic_mcp_note"] == ""


def test_unreal_editor_on_uefn_port_gets_the_move_note(tmp_path: Path, monkeypatch) -> None:
    _island, game, repo = _projects(tmp_path)
    for root in (game, repo):
        result, _ = _probe_ports(monkeypatch, owners={8000: "UnrealEditor.exe"}, root=root)
        assert result["epic_mcp_online"] is True
        assert result["epic_mcp_url"] == EPIC_MCP_DEFAULT_URL
        assert "8001" in result["epic_mcp_note"]


def test_island_only_looks_at_uefn_port(tmp_path: Path, monkeypatch) -> None:
    island, _game, _repo = _projects(tmp_path)
    result, probed = _probe_ports(monkeypatch, owners={8001: "UnrealEditor.exe"}, root=island)
    assert result["epic_mcp_reason"] == "unreachable"
    assert probed == [8000]
    blocked, _ = _probe_ports(monkeypatch, owners={8000: "UnrealEditor.exe"}, root=island)
    assert blocked["epic_mcp_reason"] == "other_editor"
    assert epic.UNREAL_MOVED_NOTE in blocked["epic_mcp_setup_steps"]


def test_plain_folder_prefers_uefn_when_both_run(tmp_path: Path, monkeypatch) -> None:
    _island, _game, repo = _projects(tmp_path)
    owners = {8000: "UnrealEditorFortnite-Win64-Shipping.exe", 8001: "UnrealEditor.exe"}
    result, _ = _probe_ports(monkeypatch, owners=owners, root=repo)
    assert result["epic_mcp_editor"] == epic.EDITOR_UEFN
    only_unreal, _ = _probe_ports(monkeypatch, owners={8001: "UnrealEditor.exe"}, root=repo)
    assert only_unreal["epic_mcp_editor"] == epic.EDITOR_UNREAL
    assert only_unreal["epic_mcp_url"] == "http://127.0.0.1:8001/mcp"


def test_row_set_by_hand_to_unreal_port_still_means_uefn(monkeypatch) -> None:
    import backend.mcp_plugins.store as store

    for row, want in (
        ("http://127.0.0.1:8001/mcp", EPIC_MCP_DEFAULT_URL),
        ("http://127.0.0.1:8123/mcp", "http://127.0.0.1:8123/mcp"),
    ):
        monkeypatch.setattr(store, "load_plugin_manifest", lambda _id: {"id": EPIC_MCP_SERVER_ID})
        monkeypatch.setattr(store, "resolve_server_block", lambda _m, row=row: {"type": "http", "url": row})
        assert epic.epic_mcp_url() == want


def test_pool_moves_unreal_editor_to_its_port() -> None:
    import json

    sent: list[tuple[str, dict]] = []

    class Session:
        async def call_tool(self, name, args):
            sent.append((name, args))

    asyncio.run(PluginClientPool()._move_unreal_editor_port(Session()))
    name, args = sent[0]
    assert name == "call_tool" and args["tool_name"] == "SetSectionProperties"
    assert args["arguments"]["sectionName"] == "ModelContextProtocolSettings"
    assert json.loads(args["arguments"]["propertiesJson"]) == {"serverPortNumber": 8001, "bAutoStartServer": True}


def test_ensure_editor_auto_start_appends_and_flips(tmp_path: Path) -> None:
    ini = tmp_path / "Editor.ini"
    ini.write_text("[/Script/Foo]\nBar=1\n", encoding="utf-8")
    assert epic.ensure_editor_auto_start(ini) is True
    text = ini.read_text(encoding="utf-8")
    assert "bAutoStartServer=True" in text
    assert epic.ensure_editor_auto_start(ini) is False
    ini.write_text("[/Script/Foo]\nbAutoStartServer=False\n", encoding="utf-8")
    assert epic.ensure_editor_auto_start(ini) is True
    assert "bAutoStartServer=True" in ini.read_text(encoding="utf-8")


def _hanging_epic_pool(monkeypatch, reply_after: float) -> tuple[PluginClientPool, list[str]]:
    """A pool whose Epic session answers after ``reply_after`` seconds; no network."""
    import backend.mcp_plugins.client_pool as cp
    from backend.tools.core import uefn_popups

    rebuild_plugin_prefix_cache([{"id": EPIC_MCP_SERVER_ID, "tool_prefix": "unreal"}])
    monkeypatch.setattr(cp, "ensure_plugin_prefix_cache", lambda: None)
    monkeypatch.setattr(epic, "probe_epic_mcp", lambda **_k: {"epic_mcp_online": True, "epic_mcp_editor": "unreal"})
    monkeypatch.setattr(uefn_popups, "blocked_call_hint", lambda: "POPUP HINT")
    monkeypatch.setattr(cp, "_EPIC_QUICK_TIMEOUT_SEC", 0.05)
    sent: list[str] = []

    class Session:
        async def call_tool(self, name, args):
            sent.append(name)
            await asyncio.sleep(reply_after)
            return "ok"

    pool = PluginClientPool()
    conn = cp.PluginConnection(plugin_id=EPIC_MCP_SERVER_ID, manifest={})

    async def _conn(_pid):
        return conn

    async def _session(_conn):
        return Session()

    monkeypatch.setattr(pool, "_get_or_create", _conn)
    monkeypatch.setattr(pool, "_ensure_session", _session)
    return pool, sent


def test_epic_call_after_a_timeout_fails_at_once(monkeypatch) -> None:
    """Nov 3 2026: one hung Epic call made every later unreal__* call wait its own 180 s."""
    pool, sent = _hanging_epic_pool(monkeypatch, reply_after=10)

    async def run() -> None:
        try:
            await pool.call_tool("unreal__list_toolsets", {})
            raise AssertionError("expected a timeout")
        except RuntimeError as exc:
            assert "timed out after 0s" in str(exc)
        started = time.monotonic()
        try:
            await pool.call_tool("unreal__describe_toolset", {})
            raise AssertionError("expected a fail-fast")
        except RuntimeError as exc:
            assert "not answering" in str(exc) and "POPUP HINT" in str(exc)
        assert time.monotonic() - started < 1.0

    asyncio.run(run())
    assert sent == ["list_toolsets"], "the call behind a timeout is never sent"


def test_epic_answer_clears_the_unanswered_mark(monkeypatch) -> None:
    pool, sent = _hanging_epic_pool(monkeypatch, reply_after=0)
    pool._epic_unanswered_at = time.time() - 61
    assert asyncio.run(pool.call_tool("unreal__list_toolsets", {})) == "ok"
    assert pool._epic_unanswered_at == 0.0 and sent == ["list_toolsets"]
