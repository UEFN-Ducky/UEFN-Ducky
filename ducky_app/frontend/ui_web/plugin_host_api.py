"""Generic plugin host helpers: LLM complete + JSON cache (not feature-specific)."""

from __future__ import annotations

import asyncio
import json
import logging
import re
import threading
from pathlib import Path
from typing import Any

from backend.skill import appdata_dir

log = logging.getLogger("uefn.plugin_host_api")

_CACHE_LOCK = threading.Lock()
_CACHE: dict[str, dict[str, Any]] = {}
_PREFS_LOCK = threading.Lock()


def _safe_plugin_id(plugin_id: str) -> str:
    pid = re.sub(r"[^a-z0-9_-]", "", (plugin_id or "").strip().lower())[:64]
    if not pid:
        raise ValueError("plugin_id required")
    return pid


def _safe_key(key: str) -> str:
    # Keep unicode letters/digits; spaces → underscore so "Scottish Gaelic" survives.
    k = re.sub(r"[^\w.\-]+", "_", (key or "").strip(), flags=re.UNICODE).strip("._-")[:64]
    if not k:
        raise ValueError("key required")
    return k


def cache_dir(plugin_id: str) -> Path:
    return appdata_dir() / "uefn_plugin_cache" / _safe_plugin_id(plugin_id)


def cache_get(plugin_id: str, key: str) -> dict[str, Any]:
    """Read plugin cache. Always prefer disk over process memory.

    Coding agents (Cursor / Claude CLI) call MCP tools in a *separate* process
    that writes the same JSON files. Sticky in-memory hits made the panel UI
    miss those updates (e.g. Duck-Tac-Toe O never appearing after a move).
    """
    pid = _safe_plugin_id(plugin_id)
    k = _safe_key(key)
    mem_key = f"{pid}:{k}"
    path = cache_dir(pid) / f"{k}.json"
    data: dict[str, Any] = {}
    if path.is_file():
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                data = raw
        except (OSError, json.JSONDecodeError, ValueError):
            data = {}
    with _CACHE_LOCK:
        _CACHE[mem_key] = data
    return dict(data)


def cache_set(plugin_id: str, key: str, data: dict[str, Any]) -> None:
    pid = _safe_plugin_id(plugin_id)
    k = _safe_key(key)
    payload = data if isinstance(data, dict) else {}
    mem_key = f"{pid}:{k}"
    path = cache_dir(pid)
    path.mkdir(parents=True, exist_ok=True)
    target = path / f"{k}.json"
    tmp = target.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=0) + "\n", encoding="utf-8")
    tmp.replace(target)
    with _CACHE_LOCK:
        _CACHE[mem_key] = dict(payload)


def cache_clear(plugin_id: str, key: str = "") -> dict[str, Any]:
    """Clear cache entries.

    - ``key=""`` — wipe the whole plugin cache
    - ``key`` exact — one file
    - ``key`` ending in ``*`` — prefix match (e.g. ``vf_chinese_*`` clears Verse
      visual translations for Chinese). Prefix is sanitized like ``_safe_key``.
    """
    pid = _safe_plugin_id(plugin_id)
    root = cache_dir(pid)
    cleared: list[str] = []
    raw = (key or "").strip()
    with _CACHE_LOCK:
        if not raw:
            prefix = f"{pid}:"
            for mk in [m for m in _CACHE if m.startswith(prefix)]:
                del _CACHE[mk]
            if root.is_dir():
                for path in root.glob("*.json"):
                    cleared.append(path.stem)
                    path.unlink(missing_ok=True)
        elif raw.endswith("*"):
            # Sanitize prefix (same alphabet as _safe_key) without requiring non-empty stem.
            pre = re.sub(r"[^\w.\-]+", "_", raw[:-1].strip(), flags=re.UNICODE).strip("._-")
            if not pre:
                raise ValueError("prefix required")
            mem_prefix = f"{pid}:{pre}"
            for mk in [m for m in _CACHE if m.startswith(mem_prefix)]:
                del _CACHE[mk]
                cleared.append(mk.split(":", 1)[1])
            if root.is_dir():
                for path in root.glob("*.json"):
                    if path.stem.startswith(pre):
                        cleared.append(path.stem)
                        path.unlink(missing_ok=True)
            # de-dupe while preserving order
            cleared = list(dict.fromkeys(cleared))
        else:
            k = _safe_key(raw)
            mem_key = f"{pid}:{k}"
            _CACHE.pop(mem_key, None)
            path = root / f"{k}.json"
            if path.is_file():
                path.unlink(missing_ok=True)
                cleared.append(k)
    return {"ok": True, "cleared": cleared}


def prefs_dir() -> Path:
    return appdata_dir() / "uefn_plugin_prefs"


def prefs_all_path() -> Path:
    return prefs_dir() / "all.json"


def prefs_all_get() -> dict[str, Any]:
    """All plugin UI prefs (same shape as localStorage uefn-plugin-ui-prefs)."""
    path = prefs_all_path()
    if not path.is_file():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(raw, dict) and not isinstance(raw, list):
            out: dict[str, Any] = {}
            for pid, slot in raw.items():
                if not isinstance(pid, str) or not isinstance(slot, dict):
                    continue
                clean = _safe_plugin_id(pid)
                out[clean] = {
                    str(k): v
                    for k, v in slot.items()
                    if isinstance(k, str)
                    and (isinstance(v, (bool, int, float, str)) or v is None)
                }
            return out
    except (OSError, json.JSONDecodeError, ValueError):
        pass
    return {}


def prefs_all_set(all_prefs: dict[str, Any]) -> dict[str, Any]:
    """Merge plugin slots into the disk bag (never delete siblings not in `all_prefs`)."""
    with _PREFS_LOCK:
        bag = prefs_all_get()
        src = all_prefs if isinstance(all_prefs, dict) else {}
        for pid, slot in src.items():
            if not isinstance(pid, str) or not isinstance(slot, dict):
                continue
            try:
                clean = _safe_plugin_id(pid)
            except ValueError:
                continue
            bag[clean] = {
                str(k): v
                for k, v in slot.items()
                if isinstance(k, str) and isinstance(v, (bool, int, float, str))
            }
        root = prefs_dir()
        root.mkdir(parents=True, exist_ok=True)
        target = prefs_all_path()
        tmp = target.with_suffix(".tmp")
        tmp.write_text(json.dumps(bag, ensure_ascii=False, indent=0) + "\n", encoding="utf-8")
        tmp.replace(target)
        return {"ok": True, "prefs": bag}


def prefs_plugin_get(plugin_id: str) -> dict[str, Any]:
    return dict(prefs_all_get().get(_safe_plugin_id(plugin_id), {}) or {})


def prefs_plugin_set(plugin_id: str, prefs: dict[str, Any]) -> dict[str, Any]:
    """Write one plugin's prefs slot without wiping other plugins."""
    pid = _safe_plugin_id(plugin_id)
    slot = {
        str(k): v
        for k, v in (prefs or {}).items()
        if isinstance(k, str) and isinstance(v, (bool, int, float, str))
    }
    return prefs_all_set({pid: slot})


def _llm_work(plugin_id: str, system_t: str, user_t: str, model: str) -> dict[str, Any]:
    from frontend.ui_web.plugin_llm import _complete_text, _resolve_api_model

    try:
        from backend.agent.batch_backends import supports_batch_complete

        provider_name, model_id = _resolve_api_model(model=str(model or "").strip())
        # Coding agents are terrible for batch UI JSON — hang / never finish.
        if not supports_batch_complete(provider_name):
            return {
                "ok": False,
                "error": (
                    "UI translation needs an API model (Settings → LLMs gateway). "
                    f"{provider_name} hangs on batch translate — change Model in Settings → Languages."
                ),
            }
        text = asyncio.run(
            _complete_text(
                provider_name=provider_name,
                model=model_id,
                system=system_t,
                user=user_t,
            )
        )
        return {"ok": True, "text": text, "provider": provider_name, "model": model_id}
    except Exception as exc:
        log.warning("plugin_llm_complete failed (%s): %s", plugin_id, exc)
        return {"ok": False, "error": str(exc) or "LLM complete failed"}


def llm_start(
    plugin_id: str,
    *,
    system: str,
    user: str,
    model: str = "",
) -> dict[str, Any]:
    """Kick off an LLM completion on a worker thread; returns immediately with job_id."""
    from backend.uefn_plugins.host import is_plugin_enabled
    from frontend.ui_web.bridge_jobs import job_start

    pid = _safe_plugin_id(plugin_id)
    if not is_plugin_enabled(pid):
        return {"ok": False, "error": f"Plugin {pid!r} disabled"}
    user_t = str(user or "")
    if not user_t.strip():
        return {"ok": False, "error": "user required"}

    system_t = str(system or "")
    model_t = str(model or "")
    return job_start(lambda: _llm_work(pid, system_t, user_t, model_t))


def llm_poll(job_id: str) -> dict[str, Any]:
    """Non-blocking status check for ``llm_start``. Bridge-safe (returns in ms)."""
    from frontend.ui_web.bridge_jobs import job_poll

    return job_poll(job_id)


def llm_cancel(job_id: str) -> dict[str, Any]:
    """Cancel a pending/running ``llm_start`` job (stops poll waiters)."""
    from frontend.ui_web.bridge_jobs import job_cancel

    return job_cancel(job_id)


def llm_complete(
    plugin_id: str,
    *,
    system: str,
    user: str,
    model: str = "",
) -> dict[str, Any]:
    """Sync wrapper — prefer ``llm_start`` + ``llm_poll`` so the bridge stays free.

    Kept for older callers; still blocks the bridge until done (up to 90s).
    """
    from frontend.ui_web.bridge_jobs import job_wait

    started = llm_start(plugin_id, system=system, user=user, model=model)
    if not started.get("ok") or not started.get("job_id"):
        return {"ok": False, "error": started.get("error") or "LLM start failed"}
    return job_wait(str(started["job_id"]), timeout=180.0)


def translate_batch_start(
    plugin_id: str,
    *,
    language: str,
    strings: dict[str, Any] | list[Any] | str,
    model: str = "",
) -> dict[str, Any]:
    """Start a UI translate batch on a worker (same path as MCP ``translate_ui_batch``)."""
    from backend.tools.translation_service import PLUGIN_ID, run_translate_ui_batch
    from backend.uefn_plugins.host import is_plugin_enabled
    from frontend.ui_web.bridge_jobs import job_start

    pid = _safe_plugin_id(plugin_id)
    if pid != PLUGIN_ID:
        return {"ok": False, "error": "translate_batch is only for the translation plugin"}
    if not is_plugin_enabled(pid):
        return {"ok": False, "error": f"Plugin {pid!r} disabled"}

    lang = str(language or "").strip()
    model_t = str(model or "").strip()
    payload = strings

    def _work() -> dict[str, Any]:
        return run_translate_ui_batch(
            lang,
            payload,
            model=model_t,
            write_cache=True,
            require_enabled=True,
        )

    return job_start(_work)


def translate_batch_poll(job_id: str) -> dict[str, Any]:
    """Poll ``translate_batch_start`` (non-blocking)."""
    from frontend.ui_web.bridge_jobs import job_poll

    return job_poll(job_id)


def _tts_work(plugin_id: str, text: str, voice_id: str) -> dict[str, Any]:
    from backend.uefn_plugins.host import get_tts_synthesizer, is_plugin_enabled

    pid = _safe_plugin_id(plugin_id)
    if not is_plugin_enabled(pid):
        return {"ok": False, "error": f"Plugin {pid!r} disabled"}
    text_t = str(text or "").strip()
    if not text_t:
        return {"ok": False, "error": "text required"}
    synth = get_tts_synthesizer(pid)
    if synth is None:
        return {"ok": False, "error": f"Plugin {pid!r} has no TTS synthesizer registered"}
    try:
        result = synth(text_t, str(voice_id or "").strip())
    except Exception as exc:
        log.warning("plugin_tts failed (%s): %s", pid, exc)
        return {"ok": False, "error": str(exc) or "TTS failed"}
    if not isinstance(result, dict):
        return {"ok": False, "error": "TTS synthesizer returned non-dict"}
    out = dict(result)
    out.setdefault("ok", True)
    return out


def tts_start(plugin_id: str, *, text: str, voice_id: str = "") -> dict[str, Any]:
    """Kick off plugin TTS on a worker; returns immediately with job_id."""
    from frontend.ui_web.bridge_jobs import job_start

    pid = str(plugin_id or "")
    text_t = str(text or "")
    voice_t = str(voice_id or "")
    return job_start(lambda: _tts_work(pid, text_t, voice_t))


def tts_poll(job_id: str) -> dict[str, Any]:
    from frontend.ui_web.bridge_jobs import job_poll

    return job_poll(job_id)


def _tts_voices_work(plugin_id: str) -> dict[str, Any]:
    from backend.uefn_plugins.host import get_tts_voices_lister, is_plugin_enabled

    pid = _safe_plugin_id(plugin_id)
    if not is_plugin_enabled(pid):
        return {"ok": False, "error": f"Plugin {pid!r} disabled", "voices": []}
    lister = get_tts_voices_lister(pid)
    if lister is None:
        # Not an error — plugin simply ships no dynamic voices (static manifest only).
        return {"ok": True, "voices": []}
    try:
        raw = lister()
    except Exception as exc:
        log.warning("plugin_tts_voices failed (%s): %s", pid, exc)
        return {"ok": False, "error": str(exc) or "list voices failed", "voices": []}
    voices: list[dict[str, str]] = []
    for item in raw or []:
        if not isinstance(item, dict):
            continue
        vid = str(item.get("id") or "").strip()
        if not vid:
            continue
        label = str(item.get("label") or vid).strip()
        voices.append({"id": vid, "label": label or vid})
    return {"ok": True, "voices": voices}


def tts_voices_start(plugin_id: str) -> dict[str, Any]:
    """Kick off dynamic voice listing on a worker; returns immediately with job_id."""
    from frontend.ui_web.bridge_jobs import job_start

    pid = str(plugin_id or "")
    return job_start(lambda: _tts_voices_work(pid))


def tts_voices_poll(job_id: str) -> dict[str, Any]:
    from frontend.ui_web.bridge_jobs import job_poll

    return job_poll(job_id)
