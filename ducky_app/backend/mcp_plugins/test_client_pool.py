"""Epic UEFN calls through the pool: one deadline for a whole expanded sequence."""

from __future__ import annotations

import asyncio
import json
import time
from contextlib import nullcontext
from types import SimpleNamespace

import pytest
from mcp.types import TextContent

from backend.bridge import call_limits
from backend.mcp_plugins import client_pool, epic, epic_array_set, registry
from backend.tools.core import uefn_modal
from frontend import uefn_events


def test_a_three_step_sequence_ends_at_one_deadline(monkeypatch) -> None:
    calls: list[dict] = []

    class Session:
        async def call_tool(self, name, step):
            calls.append(step)
            await asyncio.sleep(0.2)
            return SimpleNamespace(content=[TextContent(type="text", text="ok")], isError=False)

    async def forever():
        await asyncio.sleep(60)

    pool = client_pool.PluginClientPool()

    async def conn(_plugin_id):
        return SimpleNamespace(last_used=0.0)

    async def session(_conn):
        return Session()

    monkeypatch.setattr(pool, "_get_or_create", conn)
    monkeypatch.setattr(pool, "_ensure_session", session)
    monkeypatch.setattr(client_pool, "ensure_plugin_prefix_cache", lambda: None)
    monkeypatch.setattr(client_pool, "_blocking_popup_stays", forever)
    monkeypatch.setattr(registry, "parse_plugin_tool", lambda name: (epic.EPIC_MCP_SERVER_ID, "call_tool"))
    monkeypatch.setattr(epic, "probe_epic_mcp", lambda: {"epic_mcp_editor": "uefn"})
    monkeypatch.setattr(epic, "epic_tool_heavy", lambda name, args: False)
    monkeypatch.setattr(epic_array_set, "expand_epic_call", lambda name, args: [{"n": 1}, {"n": 2}, {"n": 3}])
    monkeypatch.setattr(call_limits, "epic_call_limit", lambda heavy: 0.3)
    monkeypatch.setattr(call_limits, "stall_reason", lambda op, limit: {"ok": False, "reason": "no_answer",
                                                                       "message": "UEFN has not answered."})
    monkeypatch.setattr(uefn_modal, "save_modal_watchdog", lambda label: nullcontext([]))
    # The real deadline event sends analytics and scans every process for UEFN's release
    # (psutil holds the GIL): ~0.25 s on this loop's time, 1.5 s on a loaded CI box.
    sent: list[tuple] = []
    monkeypatch.setattr(uefn_events, "deadline", lambda why, heavy, steps: sent.append((why["reason"], steps)))

    began = time.monotonic()
    with pytest.raises(RuntimeError) as late:
        asyncio.run(pool.call_tool("unreal__call_tool", {}))
    # A deadline per step would let all three 0.2 s steps finish (no raise, calls == 3), so
    # the asserts below prove one deadline; the clock only catches a hang (CI boxes stall).
    assert time.monotonic() - began < 2.0, "one 0.3 s deadline, not one per step"
    why = json.loads(str(late.value))
    assert why["steps_finished"] == "1 of 3" and "1 of 3 steps finished." in why["message"]
    assert calls == [{"n": 1}, {"n": 2}]  # the late step keeps running; the third is never sent
    assert sent == [("no_answer", 3)]
