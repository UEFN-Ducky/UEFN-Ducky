"""The agent host imports a plugin engine only when one of its tools is called, and sheds it again."""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import pytest

from backend.uefn_plugins.plugin_host import loader, state, tool_registry
from backend.uefn_plugins.plugin_host.lifecycle import invalidate_plugin_runtime
from frontend.agent_host import tool_server

PID = "hostshed"
TOOL = "hostshed_echo"
MODULE = loader.backend_module_name(PID)

_BACKEND = '''
from pathlib import Path


def register(api):
    log = Path(__file__).with_name("register.log")
    log.write_text(log.read_text() + "x" if log.exists() else "x")

    @api.tool(name="hostshed_echo")
    def hostshed_echo(text: str) -> dict:
        """Echo the text back."""
        return {"echo": text}
'''


def _plugin() -> tuple[Path, dict]:
    from backend.uefn_plugins.store import appdata_uefn_plugins_dir

    root = appdata_uefn_plugins_dir() / PID
    root.mkdir(parents=True, exist_ok=True)
    manifest = {"id": PID, "label": "Host shed", "version": "1.0.0", "backend": {"entry": "backend"}}
    (root / "plugin.json").write_text(json.dumps(manifest), encoding="utf-8")
    (root / "backend.py").write_text(_BACKEND, encoding="utf-8")
    (root / "register.log").unlink(missing_ok=True)
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
def host(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(tool_registry, "is_plugin_enabled", lambda pid: True)
    monkeypatch.setenv("UEFN_DUCKY_PROCESS_ROLE", "app")
    root, manifest = _plugin()
    loader._load_one(PID, root, manifest)  # the app's full load writes the schema cache
    _reset(monkeypatch, "agent_host")
    loader._load_one(PID, root, manifest)  # the host's boot: stubs only
    yield root
    _reset(monkeypatch, "app")
    with tool_server._lock:
        tool_server._deferred.pop(PID, None)
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


def _listed() -> dict:
    from backend.server import mcp

    tool = mcp._tool_manager.get_tool(TOOL)
    return {"name": tool.name, "description": tool.description, "parameters": tool.parameters}


def test_the_engine_loads_on_the_first_call_only(host) -> None:
    assert PID in tool_server.note_deferred()
    assert MODULE not in sys.modules
    assert _registers(host) == 1  # only the app's load ran register()
    assert tool_server.loaded_engines() == []
    assert "text" in _listed()["parameters"]["properties"]

    assert _call("hi") == {"echo": "hi"}
    assert MODULE in sys.modules
    assert _registers(host) == 2
    assert tool_server.loaded_engines() == [PID]


def test_shed_unloads_the_engine_and_keeps_the_same_tool(host) -> None:
    tool_server.note_deferred()
    before = _listed()
    assert _call("hi") == {"echo": "hi"}

    assert tool_server.shed_engines() == [PID]
    assert MODULE not in sys.modules
    assert isinstance(__import__("backend.server", fromlist=["mcp"]).mcp._tool_manager.get_tool(TOOL),
                      tool_registry._lazy_tool_class())
    assert _listed() == before
    assert tool_server.loaded_engines() == []

    assert _call("again") == {"echo": "again"}  # loads once more on the next call
    assert _registers(host) == 3
    assert tool_server.shed_engines() == [PID]


def test_shed_leaves_unloaded_engines_alone(host) -> None:
    tool_server.note_deferred()
    assert tool_server.shed_engines() == []
    assert _registers(host) == 1
