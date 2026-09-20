"""One-shot gateway completion for pipeline/automation {id}.complete nodes."""

from __future__ import annotations

import asyncio
from typing import Any

from backend.agent.providers.base import ProviderMessage, StreamEventKind


def complete_prompt(provider: str, prompt: str, model: str = "") -> dict[str, Any]:
    text = (prompt or "").strip()
    if not text:
        return {"ok": False, "error": "prompt required"}
    from backend.agent.secrets import get_key
    from backend.agent.providers import make_provider

    key = get_key(provider) or ""
    prov = make_provider(provider, key, model=model)

    async def _run() -> dict[str, Any]:
        chunks: list[str] = []
        async for ev in prov.stream_turn(
            system="",
            messages=[ProviderMessage(role="user", content=text)],
            tools=[],
        ):
            if ev.kind == StreamEventKind.TEXT_DELTA and ev.text:
                chunks.append(ev.text)
            if ev.kind == StreamEventKind.ERROR:
                return {"ok": False, "error": ev.error or "complete failed"}
        out = "".join(chunks)
        return {"ok": True, "text": out, "prompt": text}

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(_run())
    import concurrent.futures

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(lambda: asyncio.run(_run())).result(timeout=180)
