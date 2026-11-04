"""One-shot gateway completion for pipeline/automation {id}.complete nodes and the
workflow model nodes (Ask a model, Ask about an image, Extract data, Translate)."""

from __future__ import annotations

import asyncio
import base64
import io
import mimetypes
from pathlib import Path
from typing import Any

from backend.agent.message_attachment import MessageAttachment
from backend.agent.providers.base import ProviderMessage, StreamEventKind

_MAX_IMAGE_BYTES = 4 * 1024 * 1024
_MAX_IMAGE_SIDE = 2048


def image_attachment(path: str) -> MessageAttachment:
    """A picture for a vision model; big ones are scaled down to 2048 px first."""
    source = Path(path)
    if not source.is_file():
        raise ValueError(f"Image {source.name} isn't on this PC any more.")
    data = source.read_bytes()
    mime = mimetypes.guess_type(source.name)[0] or "image/png"
    if len(data) > _MAX_IMAGE_BYTES or mime not in ("image/png", "image/jpeg", "image/webp", "image/gif"):
        from PIL import Image

        with Image.open(source) as img:
            img.load()
            img.thumbnail((_MAX_IMAGE_SIDE, _MAX_IMAGE_SIDE))
            buf = io.BytesIO()
            img.convert("RGB").save(buf, "JPEG", quality=88)
        data, mime = buf.getvalue(), "image/jpeg"
    return MessageAttachment(kind="image", name=source.name, mime=mime, data_base64=base64.b64encode(data).decode("ascii"))


def _one_shot_for(provider: str) -> Any:
    """A coding-agent gateway's single-answer call (Cursor): its chat provider can't
    stream outside the agent, so a workflow asks it this way. None for API gateways."""
    try:
        from backend.uefn_plugins.plugin_host.gateways import get_coding_agent_registration
        from backend.uefn_plugins.plugin_host.lifecycle import is_plugin_enabled

        reg = get_coding_agent_registration(provider) or {}
        fn = reg.get("complete_one_shot")
        if callable(fn) and is_plugin_enabled(str(reg.get("plugin_id") or "")):
            return fn
    except Exception:
        pass
    return None


def complete_prompt(provider: str, prompt: str, model: str = "", *, system: str = "", images: list[str] | None = None) -> dict[str, Any]:
    text = (prompt or "").strip()
    if not text:
        return {"ok": False, "error": "prompt required"}
    one_shot = _one_shot_for(provider)
    if one_shot is not None:
        if images:
            return {"ok": False, "error": f"{provider.title()} models can't look at pictures in a workflow. Pick an API model (Anthropic, OpenAI, Google…) for this node."}
        try:
            answer = str(one_shot(model=model, system=system, user=text) or "").strip()
        except Exception as exc:  # noqa: BLE001 - the agent's own reason is the node's error
            return {"ok": False, "error": str(exc) or f"{provider} didn't answer."}
        return {"ok": True, "text": answer, "prompt": text} if answer else {"ok": False, "error": f"{provider} sent back nothing."}
    try:
        attachments = [image_attachment(path) for path in images or []]
    except (ValueError, OSError) as exc:
        return {"ok": False, "error": str(exc)}
    from backend.agent.providers import make_provider

    try:
        from backend.uefn_plugins.plugin_host.gateways import resolve_gateway_credential

        key = resolve_gateway_credential(provider)
    except Exception:
        from backend.agent.secrets import get_key

        key = get_key(provider) or ""
    prov = make_provider(provider, key, model=model)

    async def _run() -> dict[str, Any]:
        chunks: list[str] = []
        async for ev in prov.stream_turn(
            system=system,
            messages=[ProviderMessage(role="user", content=text, attachments=attachments)],
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
