"""An Epic UEFN call that never answers comes back at its deadline with why; UEFN's work goes on."""

from __future__ import annotations

import asyncio
import json
import time
from contextlib import nullcontext
from types import SimpleNamespace

import pytest

from backend.bridge import call_limits
from backend.mcp_plugins import client_pool, epic
from backend.mcp_plugins.registry import rebuild_plugin_prefix_cache

_CALL = {"toolset_name": "ValkyrieFortniteToolset.DeviceToolset", "tool_name": "SetDeviceProperty", "arguments": {}}


@pytest.fixture
def hung_epic(monkeypatch):
    """A pool whose Epic session never answers; returns (pool, state)."""
    from backend.tools.core import uefn_modal, uefn_popups

    state = {"cancelled": False, "popups": [], "ping_ok": False, "in_window": None}
    rebuild_plugin_prefix_cache([{"id": epic.EPIC_MCP_SERVER_ID, "tool_prefix": "unreal"}])
    monkeypatch.setattr(client_pool, "ensure_plugin_prefix_cache", lambda: None)
    monkeypatch.setattr(client_pool, "_EPIC_POPUP_LOOK_SEC", 60.0)  # only the deadline ends it
    monkeypatch.setattr(epic, "probe_epic_mcp", lambda **_k: {"epic_mcp_online": True, "epic_mcp_editor": "uefn"})
    monkeypatch.setattr(uefn_modal, "save_modal_watchdog", lambda _op: nullcontext([]))
    monkeypatch.setattr(uefn_popups, "list_popups", lambda: state["popups"])
    monkeypatch.setattr(call_limits, "_editor_answers", lambda: state["ping_ok"])
    monkeypatch.setattr(call_limits, "_command_in_window", lambda: state["in_window"])
    monkeypatch.setattr(call_limits, "EPIC_CALL_LIMIT_S", 0.2)

    class Session:
        async def call_tool(self, _name, _args):
            try:
                await asyncio.sleep(30)
            except asyncio.CancelledError:
                state["cancelled"] = True
                raise

    pool = client_pool.PluginClientPool()
    conn = SimpleNamespace(last_used=0.0)

    async def get_or_create(_pid):
        return conn

    async def ensure_session(_conn):
        return Session()

    monkeypatch.setattr(pool, "_get_or_create", get_or_create)
    monkeypatch.setattr(pool, "_ensure_session", ensure_session)
    return pool, state


def _reason(pool, state) -> dict:
    async def _go():
        started = time.monotonic()
        with pytest.raises(RuntimeError) as err:
            await pool.call_tool("unreal__call_tool", _CALL)
        assert time.monotonic() - started < 3  # the deadline, not the 30 s call
        await asyncio.sleep(0.05)
        assert state["cancelled"] is False  # the editor's work is never cancelled
        return json.loads(str(err.value))

    return asyncio.run(_go())


def test_a_hung_call_behind_a_popup_names_it(hung_epic) -> None:
    pool, state = hung_epic
    state["popups"] = [{"hwnd": 0x1234, "title": "Message Log", "blocking": True}]
    out = _reason(pool, state)
    assert out["ok"] is False and out["reason"] == "popup"
    assert out["hwnd"] == "0x1234" and out["title"] == "Message Log"


def test_a_hung_call_behind_a_ducky_commands_own_dialog_says_popup_not_wait(hung_epic) -> None:
    """Nov 13 2026: create_physics_asset_for_mesh put up UEFN's "Message" [OK] box, which leaves
    the main window enabled. The Epic call behind it read "game thread is busy ... Wait"."""
    pool, state = hung_epic
    state["popups"] = [{"hwnd": 0x98, "title": "Message", "blocking": False, "dialog": True}]
    state["in_window"] = {"command": "create_physics_asset_for_mesh", "seconds": 426.0}
    out = _reason(pool, state)
    assert out["reason"] == "popup" and out["command"] == "create_physics_asset_for_mesh"
    assert "426 s" in out["message"] and "uefn_popups" in out["message"] and "Do not wait" in out["message"]


def test_a_hung_call_with_a_busy_game_thread_says_editor_busy(hung_epic) -> None:
    pool, state = hung_epic
    assert _reason(pool, state)["reason"] == "editor_busy"


def test_a_hung_call_on_an_answering_editor_says_no_answer(hung_epic) -> None:
    pool, state = hung_epic
    state["ping_ok"] = True
    assert _reason(pool, state)["reason"] == "no_answer"


def test_heavy_calls_get_the_longer_limit() -> None:
    assert call_limits.epic_call_limit(False) == 120.0
    assert call_limits.epic_call_limit(True) == 300.0
