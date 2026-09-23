"""A clear search request is searched before the model is asked."""

import asyncio
from types import SimpleNamespace

from backend.agent.prompt_cache import build_cache_payload
from backend.agent.providers.base import StreamEvent, StreamEventKind
from backend.agent.runner import (
    AgentRunner,
    RunConfig,
    forced_web_search,
    with_user_request,
)
from backend.agent.tools import ToolCallResult


SENTENCE = "can you do a online seach? and show me pictuers of brainrot tukut"


def test_live_tail_ends_with_the_user_request() -> None:
    tail = with_user_request("Listener: offline on port 4200", SENTENCE)
    assert tail.endswith(f"Reply to the user's request: {SENTENCE}")
    assert tail.startswith("Listener: offline")


def test_forced_search_is_one_web_search() -> None:
    call = forced_web_search(SENTENCE, ["web_search", "web_fetch"])
    assert call is not None
    assert call.name == "web_search"
    assert call.arguments == {"query": "brainrot tukut", "images": True}
    assert forced_web_search("fix the verse compile error", ["web_search"]) is None
    assert forced_web_search(SENTENCE, ["web_fetch"]) is None


def test_step_one_searches_before_any_model_call(monkeypatch) -> None:
    order: list[str] = []
    seen: dict[str, object] = {}

    class Provider:
        async def stream_turn(self, *, messages, **_kwargs):
            order.append("model")
            seen["last"] = messages[-1].content
            yield StreamEvent(kind=StreamEventKind.TEXT_DELTA, text="found it")
            yield StreamEvent(kind=StreamEventKind.DONE, stop_reason="end_turn")

    tool = SimpleNamespace(
        name="web_search",
        description="search",
        inputSchema={"type": "object", "properties": {}},
    )

    async def tools():
        return [tool]

    async def execute(name, arguments=None, cancel_event=None):
        order.append("web_search")
        return ToolCallResult(ok=True, tool=name, data='{"results":[]}')

    def cache(*args, **kwargs):
        payload = build_cache_payload(*args, **kwargs)
        payload.dynamic_system = "Listener: offline on port 4200"
        return payload

    monkeypatch.setattr(
        "backend.uefn_plugins.host.resolve_gateway_credential", lambda _provider: "test-key"
    )
    monkeypatch.setattr("backend.agent.runner.make_provider", lambda *a, **k: Provider())
    monkeypatch.setattr("backend.agent.runner.list_mcp_tools", tools)
    monkeypatch.setattr("backend.agent.runner.select_tools", lambda *a, **k: [tool])
    monkeypatch.setattr("backend.agent.runner.execute_tool", execute)
    monkeypatch.setattr("backend.agent.runner.build_cache_payload", cache)

    runner = AgentRunner(RunConfig(provider="anthropic", model="test-model"))

    async def drive() -> None:
        async for _event in runner.run_turn(SENTENCE, []):
            pass

    asyncio.run(drive())
    assert order[0] == "web_search"
    assert "model" in order
    assert order.index("web_search") < order.index("model")
    last = str(seen["last"])
    assert last.endswith(f"Reply to the user's request: {SENTENCE}")
