"""The per-turn skill hint rides in the live-context tail, never in the cached system prompt."""

import asyncio
from types import SimpleNamespace

from backend.agent.prompt_cache import build_cache_payload
from backend.agent.providers.base import StreamEvent, StreamEventKind
from backend.agent.runner import AgentRunner, RunConfig

HINT = 'Relevant skills for this request (read before acting): npc/ranged > Archer — skill_read_subskill("npc", "ranged")'


def _drive(monkeypatch, texts: list[str]) -> list[dict]:
    seen: list[dict] = []

    class Provider:
        async def stream_turn(self, *, messages, system=None, **_kwargs):
            seen.append({"system": system, "last": str(messages[-1].content)})
            yield StreamEvent(kind=StreamEventKind.TEXT_DELTA, text="ok")
            yield StreamEvent(kind=StreamEventKind.DONE, stop_reason="end_turn")

    tool = SimpleNamespace(name="ducky_get_status", description="status", inputSchema={"type": "object", "properties": {}})

    async def tools():
        return [tool]

    def cache(*args, **kwargs):
        payload = build_cache_payload(*args, **kwargs)
        payload.dynamic_system = "Listener: offline"
        return payload

    monkeypatch.setattr("backend.uefn_plugins.host.resolve_gateway_credential", lambda _provider, **_k: "test-key")
    monkeypatch.setattr("backend.agent.runner.make_provider", lambda *a, **k: Provider())
    monkeypatch.setattr("backend.agent.runner.list_mcp_tools", tools)
    monkeypatch.setattr("backend.agent.runner.select_tools", lambda *a, **k: [tool])
    monkeypatch.setattr("backend.agent.runner.build_cache_payload", cache)
    monkeypatch.setattr("backend.search.hints.skill_hint", lambda text, conv=None: HINT if "archer" in text else "")

    runner = AgentRunner(RunConfig(provider="anthropic", model="test-model"))

    async def drive() -> None:
        for text in texts:
            async for _event in runner.run_turn(text, []):
                pass

    asyncio.run(drive())
    return seen


def test_hint_is_in_the_turn_and_the_system_prompt_does_not_change(monkeypatch) -> None:
    seen = _drive(monkeypatch, ["make an archer enemy", "continue"])
    assert HINT in seen[0]["last"]
    assert seen[0]["last"].endswith("Reply to the user's request: make an archer enemy")
    assert HINT not in seen[1]["last"]
    assert seen[0]["system"] == seen[1]["system"]
    assert HINT not in str(seen[0]["system"])
