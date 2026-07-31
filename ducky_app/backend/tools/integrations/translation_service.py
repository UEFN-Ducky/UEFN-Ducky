"""Shared UI-translation pipeline used by MCP tools and the Translation plugin host.

One path: batch strings in → LLM JSON out → merge into plugin cache. Agents call
``translate_ui_batch``; the Languages UI calls the same function via the panel API.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Any

log = logging.getLogger("uefn.translation_service")

PLUGIN_ID = "translation"

_SYSTEM = (
    "You are a professional UI localizer for a desktop app (UEFN Ducky).\n\n"
    "You receive ONE BATCH: a JSON object. Keys and values are the SAME English UI strings.\n"
    "Translate EVERY value into the target language. Return ONLY JSON with the SAME keys.\n"
    "No markdown fences, no commentary, no missing keys.\n\n"
    "Rules:\n"
    "- Preserve placeholders like {name}, {{count}}, %s, %d\n"
    "- Preserve ellipses (… / ...), em-dashes, and leading/trailing whitespace shape\n"
    "- ALWAYS translate UI chrome labels even if they contain product words\n"
    "- Keep only standalone product/code tokens untranslated when the WHOLE string is just that token\n"
    "- Do not add quotes around values unless the source had them\n"
    "- Keep roughly the same length when possible (UI labels are short)\n"
)

_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```\s*$", re.IGNORECASE)


def _strip_fences(text: str) -> str:
    raw = (text or "").strip()
    if raw.startswith("```"):
        raw = _FENCE_RE.sub("", raw).strip()
    return raw


def parse_batch_response(text: str, batch_keys: list[str]) -> tuple[dict[str, str], list[str]]:
    """Parse model JSON into (translations, missing_keys)."""
    data = json.loads(_strip_fences(text))
    if not isinstance(data, dict):
        raise ValueError("Translation response must be a JSON object")
    out: dict[str, str] = {}
    missing: list[str] = []
    for i, src in enumerate(batch_keys):
        val = data.get(src)
        if not isinstance(val, str) or not val.strip():
            val = data.get(str(i))
            if val is None and i in data:
                val = data[i]
        if not isinstance(val, str) or not val.strip():
            missing.append(src)
            continue
        out[src] = val
    if not out:
        raise ValueError("No translations in model response")
    if len(missing) > len(batch_keys) * 0.5:
        raise ValueError(f"Model missed too many keys ({len(missing)}/{len(batch_keys)})")
    return out, missing


def _normalize_strings(strings: Any) -> dict[str, str]:
    if isinstance(strings, str):
        strings = json.loads(strings)
    if not isinstance(strings, dict):
        raise ValueError("strings must be a JSON object of English → English (same keys/values)")
    out: dict[str, str] = {}
    for k, v in strings.items():
        key = str(k or "").strip()
        if not key:
            continue
        # Accept either {"Hello":"Hello"} or {"Hello":""} — value ignored for source.
        out[key] = key
    if not out:
        raise ValueError("strings is empty")
    if len(out) > 40:
        raise ValueError("Batch too large (max 40). Split into smaller translate_ui_batch calls.")
    return out


def run_translate_ui_batch(
    language: str,
    strings: Any,
    *,
    model: str = "",
    write_cache: bool = True,
    require_enabled: bool = True,
) -> dict[str, Any]:
    """Translate a UI string batch into ``language`` and optionally merge into cache.

    Returns ``{ok, language, map, missing, provider?, model?, error?}``.
    """
    from backend.uefn_plugins.host import is_plugin_enabled
    from frontend.ui_web import plugin_host_api as pha
    from frontend.ui_web.plugin_llm import _complete_text, _resolve_api_model

    lang = str(language or "").strip()
    if not lang:
        return {"ok": False, "error": "language required"}
    if lang.lower() in {"en", "eng", "english"}:
        return {"ok": False, "error": "language must not be English"}

    if require_enabled and not is_plugin_enabled(PLUGIN_ID):
        return {"ok": False, "error": "Translation plugin is disabled — enable it in Settings → Store"}

    try:
        batch = _normalize_strings(strings)
    except Exception as exc:
        return {"ok": False, "error": str(exc) or "invalid strings"}

    try:
        provider_name, model_id = _resolve_api_model(model=str(model or "").strip())
    except Exception as exc:
        return {"ok": False, "error": str(exc) or "model resolve failed"}

    from backend.agent.batch_backends import supports_batch_complete

    if not supports_batch_complete(provider_name):
        return {
            "ok": False,
            "error": (
                "UI translation needs an API model (Settings → LLMs gateway). "
                f"{provider_name} hangs on batch translate — change Model in Settings → Languages."
            ),
        }

    keys = list(batch.keys())
    payload = {k: k for k in keys}
    user = (
        f"Target language: {lang}\n"
        f"Batch size: {len(keys)}\n"
        "Category hint: UI chrome labels\n\n"
        "Return ONLY JSON with the SAME keys; translate each value:\n"
        f"{json.dumps(payload, ensure_ascii=False)}"
    )

    try:
        text = asyncio.run(
            _complete_text(
                provider_name=provider_name,
                model=model_id,
                system=_SYSTEM,
                user=user,
                usage_agent="translation",
            )
        )
        mapping, missing = parse_batch_response(text, keys)
    except Exception as exc:
        log.warning("run_translate_ui_batch failed (%s): %s", lang, exc)
        return {"ok": False, "error": str(exc) or "Translation failed", "language": lang}

    if write_cache:
        try:
            existing = pha.cache_get(PLUGIN_ID, lang) or {}
            if not isinstance(existing, dict):
                existing = {}
            merged = dict(existing)
            merged.update(mapping)
            pha.cache_set(PLUGIN_ID, lang, merged)
        except Exception as exc:
            log.warning("run_translate_ui_batch cache write failed: %s", exc)

    return {
        "ok": True,
        "language": lang,
        "map": mapping,
        "missing": missing,
        "provider": provider_name,
        "model": model_id,
    }
