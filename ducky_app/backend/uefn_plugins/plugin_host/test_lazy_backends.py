"""Helper processes list a plugin's tools from its cached schemas and load it on first call."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from backend.uefn_plugins.plugin_host import loader, state, tool_registry
from backend.uefn_plugins.plugin_host.lifecycle import invalidate_plugin_runtime

PID = "lazyprobe"
TOOL = "lazyprobe_echo"

_BACKEND = '''
from pathlib import Path


def register(api):
    log = Path(__file__).with_name("register.log")
    log.write_text(log.read_text() + "x" if log.exists() else "x")
    {extra}

    @api.tool(name="lazyprobe_echo")
    def lazyprobe_echo(text: str) -> dict:
        """Echo the text back."""
        return {{"echo": text}}
'''


def _plugin(extra: str = "", contributes: dict | None = None) -> tuple[Path, dict]:
    from backend.uefn_plugins.store import appdata_uefn_plugins_dir

    root = appdata_uefn_plugins_dir() / PID
    root.mkdir(parents=True, exist_ok=True)
    manifest = {"id": PID, "label": "Lazy probe", "version": "1.0.0", "backend": {"entry": "backend"}}
    if contributes:
        manifest["contributes"] = contributes
    (root / "plugin.json").write_text(json.dumps(manifest), encoding="utf-8")
    (root / "backend.py").write_text(_BACKEND.format(extra=extra), encoding="utf-8")
    return root, manifest


def _registers(root: Path) -> int:
    log = root / "register.log"
    return len(log.read_text()) if log.exists() else 0


def _reset(monkeypatch: pytest.MonkeyPatch, role: str) -> None:
    invalidate_plugin_runtime(PID)
    from backend.server import mcp

    mcp._tool_manager._tools.pop(TOOL, None)
    with tool_registry._LAZY_GUARD:
        tool_registry._LAZY_PENDING.pop(PID, None)
    monkeypatch.setenv("UEFN_DUCKY_PROCESS_ROLE", role)


@pytest.fixture
def probe(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(tool_registry, "is_plugin_enabled", lambda pid: True)
    monkeypatch.setenv("UEFN_DUCKY_PROCESS_ROLE", "app")
    yield
    _reset(monkeypatch, "app")
    with state._LOCK:
        state._API_TOOL_REGISTRY.pop(PID, None)
        state._CONTRIBUTIONS["agent_tools"].pop(PID, None)
        state._PLUGIN_TOOL_NAMES.discard(TOOL)
        state._PLUGIN_TOOL_OWNER.pop(TOOL, None)
        state._TOOL_LAST_OWNER.pop(TOOL, None)
        state._PLUGIN_HOST_ONLY_TOOLS.discard(TOOL)
        tool_registry._rebuild_intent_row(PID, set())


def _call(text: str):
    from backend.server import mcp

    return asyncio.run(mcp._tool_manager.call_tool(TOOL, {"text": text}, convert_result=False))


def test_the_app_registers_at_load_and_saves_the_schemas(probe) -> None:
    root, manifest = _plugin()
    loader._load_one(PID, root, manifest)
    assert _registers(root) == 1
    data = tool_registry.lazy_tool_schemas(PID, root, manifest)
    assert data is not None and data["lazy"] is True
    [row] = data["tools"]
    assert row["name"] == TOOL and "text" in row["parameters"]["properties"]


def test_a_tool_server_lists_tools_and_registers_on_first_call(probe, monkeypatch) -> None:
    root, manifest = _plugin()
    loader._load_one(PID, root, manifest)  # the app writes the cache
    _reset(monkeypatch, "tool_server")

    loader._load_one(PID, root, manifest)
    assert _registers(root) == 1  # register() did not run in the tool server
    assert tool_registry.lazy_pending(PID)
    from backend.server import mcp

    stub = mcp._tool_manager.get_tool(TOOL)
    assert isinstance(stub, tool_registry._lazy_tool_class())
    assert "text" in stub.parameters["properties"]  # schema came from the cache file
    assert TOOL in tool_registry.plugin_tool_names()

    assert _call("hi") == {"echo": "hi"}
    assert _registers(root) == 2  # first call ran register() once
    assert not tool_registry.lazy_pending(PID)
    assert _call("again") == {"echo": "again"}
    assert _registers(root) == 2


def test_a_tool_server_without_a_cache_loads_now_and_writes_it(probe, monkeypatch) -> None:
    root, manifest = _plugin()
    monkeypatch.setenv("UEFN_DUCKY_PROCESS_ROLE", "tool_server")
    loader._load_one(PID, root, manifest)
    assert _registers(root) == 1
    assert tool_registry.lazy_tool_schemas(PID, root, manifest) is not None


_NODE = "api.register_pipeline_node('lazyprobe.node', lambda inputs=None, **kw: {'node': 'ran'})"


def test_a_tool_server_runs_a_cached_node_by_loading_the_plugin(probe, monkeypatch) -> None:
    from backend.automations.plugin import clear_for_plugin, get_handler

    root, manifest = _plugin(extra=_NODE)
    loader._load_one(PID, root, manifest)
    assert tool_registry.lazy_tool_schemas(PID, root, manifest)["nodes"] == ["lazyprobe.node"]
    _reset(monkeypatch, "tool_server")
    clear_for_plugin(PID)
    try:
        loader._load_one(PID, root, manifest)
        assert _registers(root) == 1  # still lazy: nodes are served as stubs
        stub = get_handler("lazyprobe.node")
        assert getattr(stub, "_ducky_lazy_node", False)
        assert stub({}) == {"node": "ran"}
        assert _registers(root) == 2
        real = get_handler("lazyprobe.node")
        assert not getattr(real, "_ducky_lazy_node", False)
    finally:
        clear_for_plugin(PID)


def test_app_only_hooks_stay_lazy(probe, monkeypatch) -> None:
    root, manifest = _plugin(extra="api.register_panel_rpc('status', lambda: {}); api.register_secret_test('k', lambda v: True)")
    loader._load_one(PID, root, manifest)
    data = tool_registry.lazy_tool_schemas(PID, root, manifest)
    assert data is not None and data["unsafe_api"] == []


def test_panel_rpc_behind_project_sources_stays_eager(probe, monkeypatch) -> None:
    root, manifest = _plugin(
        extra="api.register_panel_rpc('projects', lambda: [])",
        contributes={"project.sources": [{"id": "p", "rpc": "projects"}]},
    )
    loader._load_one(PID, root, manifest)
    assert tool_registry.lazy_tool_schemas(PID, root, manifest) is None


def _start_load(root: Path, manifest: dict) -> None:
    """_load_one as the app's start runs it (on the plugin load thread)."""
    state._LOAD_THREAD_STATE.in_load = True
    try:
        loader._load_one(PID, root, manifest)
    finally:
        state._LOAD_THREAD_STATE.in_load = False


def test_the_app_defers_a_tools_only_plugin_until_its_first_call(probe, monkeypatch) -> None:
    root, manifest = _plugin()
    loader._load_one(PID, root, manifest)  # first load: no cache yet, so it registers and writes one
    assert "tool" in tool_registry.lazy_tool_schemas(PID, root, manifest)["api_calls"]
    _reset(monkeypatch, "app")

    _start_load(root, manifest)
    assert _registers(root) == 1  # the app window did not run register() at boot
    assert tool_registry.lazy_pending(PID)
    assert _call("hi") == {"echo": "hi"}
    assert _registers(root) == 2
    assert not tool_registry.lazy_pending(PID)


def test_the_app_loads_a_deferred_plugin_on_its_first_panel_call(probe, monkeypatch) -> None:
    from backend.uefn_plugins.plugin_host import panel_rpc

    monkeypatch.setattr(panel_rpc, "is_plugin_enabled", lambda pid: True)
    root, manifest = _plugin(extra="api.register_panel_rpc('status', lambda: {'ok': 1})")
    loader._load_one(PID, root, manifest)
    _reset(monkeypatch, "app")
    _start_load(root, manifest)
    assert _registers(root) == 1 and tool_registry.lazy_pending(PID)

    assert panel_rpc.call_panel_rpc(PID, "status") == {"ok": 1}
    assert _registers(root) == 2
    assert not tool_registry.lazy_pending(PID)


@pytest.mark.parametrize("extra", ["api.is_app()", "api.register_secret_test('k', lambda v: True)"])
def test_the_app_keeps_loading_a_plugin_it_needs_at_boot(probe, monkeypatch, extra) -> None:
    root, manifest = _plugin(extra=extra)
    loader._load_one(PID, root, manifest)
    assert tool_registry.lazy_tool_schemas(PID, root, manifest) is not None  # helpers may still defer it
    _reset(monkeypatch, "app")
    _start_load(root, manifest)
    assert _registers(root) == 2
    assert not tool_registry.lazy_pending(PID)


def test_the_app_loads_eagerly_from_a_cache_without_api_calls(probe, monkeypatch) -> None:
    root, manifest = _plugin()
    loader._load_one(PID, root, manifest)
    path = tool_registry._schema_path(PID, manifest)
    data = json.loads(path.read_text(encoding="utf-8"))
    data.pop("api_calls")  # written by an older Ducky
    path.write_text(json.dumps(data), encoding="utf-8")
    _reset(monkeypatch, "app")
    _start_load(root, manifest)
    assert _registers(root) == 2
    assert "tool" in tool_registry.lazy_tool_schemas(PID, root, manifest)["api_calls"]


def test_the_start_does_not_pre_import_a_deferred_plugin(probe, monkeypatch) -> None:
    import threading

    root, manifest = _plugin()
    loader._load_one(PID, root, manifest)
    _reset(monkeypatch, "app")
    imported = []
    monkeypatch.setattr(loader, "_import_backend", lambda *a: imported.append(a))
    worker = threading.Thread(target=loader._warm_plugin_backend, args=(PID, root, manifest))
    worker.start()  # boot.py runs it on a pool thread
    worker.join()
    assert imported == []


def test_an_enable_or_reload_in_the_app_still_loads_at_once(probe, monkeypatch) -> None:
    root, manifest = _plugin()
    loader._load_one(PID, root, manifest)
    _reset(monkeypatch, "app")
    loader._load_one(PID, root, manifest)  # a Store enable worker, not the start's load
    assert _registers(root) == 2
    assert not tool_registry.lazy_pending(PID)


def test_disabling_a_deferred_plugin_forgets_it(probe, monkeypatch) -> None:
    root, manifest = _plugin()
    loader._load_one(PID, root, manifest)
    _reset(monkeypatch, "app")
    _start_load(root, manifest)
    assert tool_registry.lazy_pending(PID)
    invalidate_plugin_runtime(PID)
    assert not tool_registry.lazy_pending(PID)
    from backend.server import mcp

    assert mcp._tool_manager.get_tool(TOOL) is None


def test_a_plugin_that_registers_more_than_tools_stays_eager(probe, monkeypatch) -> None:
    root, manifest = _plugin(extra="api.register_tts(lambda text, **kw: b'')")
    loader._load_one(PID, root, manifest)
    assert tool_registry.lazy_tool_schemas(PID, root, manifest) is None
    _reset(monkeypatch, "tool_server")
    loader._load_one(PID, root, manifest)
    assert _registers(root) == 2
