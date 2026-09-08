"""Nested-MCP tool annotations survive namespacing, so unreal__* can be classified."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from mcp.types import Tool, ToolAnnotations

from backend.mcp_plugins.client_pool import PluginClientPool
from backend.workspace.editor_ops import MUT_OPAQUE, MUT_READ, MUT_WRITE, classify_plugin_tool


class FakeSession:
    def __init__(self, tools: list[Tool]) -> None:
        self._tools = tools

    async def list_tools(self):
        return SimpleNamespace(tools=self._tools)


@pytest.fixture
def pool(monkeypatch):
    p = PluginClientPool()
    conn = SimpleNamespace(manifest={"id": "unreal", "tool_prefix": "unreal"}, last_used=0.0)

    async def _get_or_create(plugin_id: str):
        return conn

    monkeypatch.setattr(p, "_get_or_create", _get_or_create)
    monkeypatch.setattr("backend.mcp_plugins.client_pool.namespace_tool_name",
                        lambda manifest, name: f"unreal__{name}")
    return p, conn


def list_tools(pool_and_conn, tools: list[Tool], monkeypatch) -> list[Tool]:
    p, _conn = pool_and_conn

    async def _ensure_session(conn):
        return FakeSession(tools)

    monkeypatch.setattr(p, "_ensure_session", _ensure_session)
    return asyncio.run(p.list_tools_for_plugin("unreal"))


def test_annotations_and_output_schema_are_preserved(pool, monkeypatch) -> None:
    p, _ = pool
    out = list_tools(
        pool,
        [
            Tool(
                name="get_all_actors",
                description="read the level",
                inputSchema={"type": "object"},
                annotations=ToolAnnotations(readOnlyHint=True),
                outputSchema={"type": "object"},
            ),
            Tool(
                name="spawn_device",
                description="place a device",
                inputSchema={"type": "object"},
                annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False),
            ),
        ],
        monkeypatch,
    )
    assert [t.name for t in out] == ["unreal__get_all_actors", "unreal__spawn_device"]
    assert out[0].annotations is not None and out[0].annotations.readOnlyHint is True
    assert out[0].outputSchema == {"type": "object"}
    assert out[1].annotations is not None and out[1].annotations.readOnlyHint is False

    # And the pool can hand them back by namespaced name for classification.
    assert p.annotations_for("unreal__get_all_actors").readOnlyHint is True
    assert classify_plugin_tool("unreal__get_all_actors", p.annotations_for("unreal__get_all_actors")).mutates == MUT_READ
    assert classify_plugin_tool("unreal__spawn_device", p.annotations_for("unreal__spawn_device")).mutates == MUT_WRITE


def test_a_server_without_annotations_still_works(pool, monkeypatch) -> None:
    p, _ = pool
    out = list_tools(
        pool,
        [Tool(name="do_thing", description="", inputSchema={"type": "object"})],
        monkeypatch,
    )
    assert out[0].name == "unreal__do_thing"
    assert out[0].annotations is None
    assert p.annotations_for("unreal__do_thing") is None
    # No annotations and no read-ish name: recorded rather than assumed harmless.
    assert classify_plugin_tool("unreal__do_thing", None).mutates == MUT_OPAQUE


def test_invalidating_the_cache_drops_annotations(pool, monkeypatch) -> None:
    p, _ = pool
    list_tools(
        pool,
        [Tool(name="x", description="", inputSchema={"type": "object"},
              annotations=ToolAnnotations(readOnlyHint=True))],
        monkeypatch,
    )
    assert p.annotations_for("unreal__x") is not None
    p.invalidate_tools_cache()
    assert p.annotations_for("unreal__x") is None
