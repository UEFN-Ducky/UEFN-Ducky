"""Cheap spoken-summary prompt + LLM call for voice replies."""

from __future__ import annotations

from typing import Any

_SYSTEM = (
    "You are the spoken voice of the assistant. "
    "Answer conversationally in 1-4 plain sentences. "
    "NEVER read code, file paths, or symbols aloud — "
    "instead say in plain words what was done or what it means. "
    "No markdown, no bullet lists, no quotes around the whole answer."
)

_VERBATIM_MAX = 200


def build_spoken_summary_prompt(assistant_text: str) -> tuple[str, str] | None:
    """Return (system, user) for summarization, or None when the reply is short enough to read verbatim."""
    text = (assistant_text or "").strip()
    if not text:
        return None
    # Always summarize if the reply looks code-heavy, even when short.
    looks_codey = "```" in text or any(
        text.lstrip().startswith(p)
        for p in ("def ", "class ", "function ", "import ", "from ", "const ", "let ", "#include")
    )
    if len(text) <= _VERBATIM_MAX and not looks_codey:
        return None
    user = (
        "Turn the following assistant reply into a short spoken response "
        "(1-4 conversational sentences, no code):\n\n"
        f"{text[:12000]}"
    )
    return _SYSTEM, user


def summarize_for_speech(assistant_text: str, model: str = "") -> dict[str, Any]:
    """Produce text to speak. Short plain replies pass through; longer/codey ones use the voice model."""
    text = (assistant_text or "").strip()
    if not text:
        return {"ok": False, "error": "empty reply"}
    prompt = build_spoken_summary_prompt(text)
    if prompt is None:
        return {"ok": True, "text": text, "verbatim": True}

    system_t, user_t = prompt
    model_t = (model or "").strip()
    try:
        from frontend.ui_web.plugin_llm import _complete_text, _resolve_api_model
        import asyncio

        from backend.agent.batch_backends import supports_batch_complete

        provider_name, model_id = _resolve_api_model(model=model_t)
        if not supports_batch_complete(provider_name):
            return {
                "ok": False,
                "error": (
                    "Voice summary needs an API model (Settings → LLMs gateway). "
                    "Pick a cheap model in Settings → LLMs → Voice."
                ),
            }
        spoken = asyncio.run(
            _complete_text(
                provider_name=provider_name,
                model=model_id,
                system=system_t,
                user=user_t,
                usage_agent="voice",
            )
        )
        spoken = (spoken or "").strip()
        if not spoken:
            return {"ok": False, "error": "empty spoken summary"}
        return {
            "ok": True,
            "text": spoken,
            "verbatim": False,
            "provider": provider_name,
            "model": model_id,
        }
    except Exception as exc:
        return {"ok": False, "error": str(exc) or "spoken summary failed"}
