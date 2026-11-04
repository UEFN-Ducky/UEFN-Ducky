"""Services a plugin registers for the app: TTS, media makers, Connections and Online
rows, and Settings "Test" for its API keys."""

from __future__ import annotations

import json
import time
from typing import Any, Mapping


def get_tts_synthesizer(plugin_id: str) -> Any | None:
    """Return the synthesizer callable registered by ``api.register_tts``."""
    from backend.uefn_plugins.store import normalize_plugin_id

    try:
        pid = normalize_plugin_id(plugin_id)
    except ValueError:
        return None
    with _LOCK:
        return _TTS_SYNTHESIZERS.get(pid)


def register_tts_synthesizer(plugin_id: str, fn: Any) -> None:
    """Host-side register for plugin ``register(api).register_tts(fn)``."""
    from backend.uefn_plugins.store import normalize_plugin_id

    pid = normalize_plugin_id(plugin_id)
    if not callable(fn):
        raise TypeError("TTS synthesizer must be callable")
    with _LOCK:
        _TTS_SYNTHESIZERS[pid] = fn


def get_tts_voices_lister(plugin_id: str) -> Any | None:
    """Return the dynamic-voices callable registered by ``api.register_tts_voices``."""
    from backend.uefn_plugins.store import normalize_plugin_id

    try:
        pid = normalize_plugin_id(plugin_id)
    except ValueError:
        return None
    with _LOCK:
        return _TTS_VOICE_LISTERS.get(pid)


def register_tts_voices_lister(plugin_id: str, fn: Any) -> None:
    """Host-side register for plugin ``register(api).register_tts_voices(fn)``."""
    from backend.uefn_plugins.store import normalize_plugin_id

    pid = normalize_plugin_id(plugin_id)
    if not callable(fn):
        raise TypeError("TTS voices lister must be callable")
    with _LOCK:
        _TTS_VOICE_LISTERS[pid] = fn


def register_media_generator(
    plugin_id: str, kind: str, fn: Any, describe: dict[str, Any] | None = None
) -> None:
    """Host-side register for ``api.register_media_generator(kind, fn, describe)``.

    ``fn(prompt, **options)`` returns the made media (``{path|url|b64, mime}``).
    ``describe``: ``{label, secret_key?, models?, ...}``; ``secret_key`` names the saved
    credential that must exist for the maker to be offered (none = always offered).
    """
    from backend.uefn_plugins.store import normalize_plugin_id

    pid = normalize_plugin_id(plugin_id)
    if kind not in MEDIA_KINDS:
        raise ValueError(f"media kind must be one of {', '.join(MEDIA_KINDS)}")
    if not callable(fn):
        raise TypeError("media generator must be callable")
    with _LOCK:
        _MEDIA_GENERATORS[(kind, pid)] = {"fn": fn, "describe": dict(describe or {})}


def media_generators(kind: str) -> list[dict[str, Any]]:
    """Makers for ``kind`` whose plugin is enabled and whose saved key is present."""
    from backend.agent.secrets import get_key

    with _LOCK:
        rows = [(pid, dict(r)) for (k, pid), r in _MEDIA_GENERATORS.items() if k == kind]
    out: list[dict[str, Any]] = []
    for pid, row in sorted(rows, key=lambda x: x[0]):
        desc = row["describe"]
        secret = str(desc.get("secret_key") or "").strip()
        if not is_plugin_enabled(pid) or (secret and not (get_key(secret) or "").strip()):
            continue
        out.append({"plugin_id": pid, "kind": kind, "fn": row["fn"], **desc})
    return out


def register_connection_probe(
    plugin_id: str,
    fn: Any,
    *,
    label: str = "",
    program: str = "",
) -> None:
    """Header Connections row. ``fn()`` must be cheap (no editor work)."""
    from backend.uefn_plugins.store import normalize_plugin_id

    pid = normalize_plugin_id(plugin_id)
    if not callable(fn):
        raise TypeError("connection probe must be callable")
    with _LOCK:
        _CONNECTION_PROBES[pid] = {
            "label": str(label or "").strip() or pid,
            "program": str(program or "").strip() or pid,
            "fn": fn,
        }
        _CONNECTION_CACHE.pop(pid, None)


def _normalize_connection_row(pid: str, spec: Mapping[str, Any], raw: Any) -> dict[str, Any]:
    label = str(spec.get("label") or pid)
    program = str(spec.get("program") or pid)
    data: Any = raw
    if isinstance(raw, str):
        text = raw.strip()
        try:
            data = json.loads(text)
        except (json.JSONDecodeError, TypeError):
            low = text.lower()
            online = not (low.startswith("error") or "disconnect" in low or "offline" in low)
            return {
                "id": pid,
                "program": program,
                "label": label,
                "online": online,
                "warn": False,
                "detail": text[:160] or ("Connected" if online else "Offline"),
            }
    if isinstance(data, Mapping):
        online = data.get("connected")
        if online is None:
            online = data.get("online")
        if online is None:
            online = data.get("ok")
        warn = bool(data.get("warn"))
        detail = str(data.get("detail") or data.get("hint") or data.get("state") or "")
        row_label = str(data.get("label") or label)
        return {
            "id": pid,
            "program": str(data.get("program") or program),
            "label": row_label,
            "online": bool(online),
            "warn": warn,
            "detail": detail[:160] or ("Connected" if online else "Offline"),
        }
    online = bool(data)
    return {
        "id": pid,
        "program": program,
        "label": label,
        "online": online,
        "warn": False,
        "detail": "Connected" if online else "Offline",
    }


def plugin_connection_rows() -> list[dict[str, Any]]:
    """Live plugin MCP rows for the Connections menu. Never raises."""
    now = time.monotonic()
    with _LOCK:
        items = list(_CONNECTION_PROBES.items())
    rows: list[dict[str, Any]] = []
    for pid, spec in items:
        if not is_plugin_enabled(pid):
            continue
        with _LOCK:
            cached = _CONNECTION_CACHE.get(pid)
        if cached and (now - cached[0]) < _CONNECTION_CACHE_TTL_S:
            rows.append(dict(cached[1]))
            continue
        fn = spec.get("fn")
        try:
            raw = fn() if callable(fn) else False
        except Exception as exc:  # noqa: BLE001 — a dead plugin must not blank the menu
            row = {
                "id": pid,
                "program": str(spec.get("program") or pid),
                "label": str(spec.get("label") or pid),
                "online": False,
                "warn": False,
                "detail": str(exc)[:160],
            }
            with _LOCK:
                _CONNECTION_CACHE[pid] = (now, row)
            rows.append(row)
            continue
        row = _normalize_connection_row(pid, spec, raw)
        with _LOCK:
            _CONNECTION_CACHE[pid] = (now, row)
        rows.append(row)
    return rows


def plugin_connection_for_program(program: str) -> dict[str, Any] | None:
    want = (program or "").strip()
    if not want:
        return None
    for row in plugin_connection_rows():
        if row.get("program") == want or row.get("id") == want:
            return row
    return None


def register_people_provider(plugin_id: str, fn: Any, *, label: str = "") -> None:
    """Header Online tab section. ``fn()`` must be cheap (cached data, no network wait)."""
    from backend.uefn_plugins.store import normalize_plugin_id

    pid = normalize_plugin_id(plugin_id)
    if not callable(fn):
        raise TypeError("people provider must be callable")
    with _LOCK:
        _PEOPLE_PROVIDERS[pid] = {"label": str(label or "").strip() or pid, "fn": fn}
        _PEOPLE_CACHE.pop(pid, None)


def plugin_people_sections() -> list[dict[str, Any]]:
    """``[{id, label, people: [{id, name, detail, online}]}]`` from enabled plugins. Never raises."""
    now = time.monotonic()
    with _LOCK:
        items = list(_PEOPLE_PROVIDERS.items())
    sections: list[dict[str, Any]] = []
    for pid, spec in items:
        if not is_plugin_enabled(pid):
            continue
        with _LOCK:
            cached = _PEOPLE_CACHE.get(pid)
        if cached and (now - cached[0]) < _CONNECTION_CACHE_TTL_S:
            sections.append(cached[1])
            continue
        try:
            raw = spec["fn"]()
        except Exception:  # noqa: BLE001 — a dead plugin must not blank the menu
            raw = []
        people = []
        for row in raw if isinstance(raw, list) else []:
            name = str(row.get("name") or "").strip() if isinstance(row, Mapping) else ""
            if name:
                people.append({
                    "id": str(row.get("id") or name)[:120],
                    "name": name[:80],
                    "detail": str(row.get("detail") or "")[:160],
                    "online": bool(row.get("online")),
                })
        section = {"id": pid, "label": spec["label"], "people": people[:50]}
        with _LOCK:
            _PEOPLE_CACHE[pid] = (now, section)
        sections.append(section)
    return sections


def attach_plugin_connections(status: Mapping[str, Any]) -> dict[str, Any]:
    """The header status menu's plugin rows: Connections, and the Online tab's people."""
    out = dict(status)
    out["plugin_connections"] = plugin_connection_rows()
    out["plugin_people"] = plugin_people_sections()
    return out


def register_secret_tester(plugin_id: str, secret_key: str, test_fn: Any) -> None:
    """Register ``test_fn(api_key: str) -> {ok, detail}`` for a plugin secret field."""
    from backend.uefn_plugins.store import normalize_plugin_id

    pid = normalize_plugin_id(plugin_id)
    key = str(secret_key or "").strip()
    if not key:
        raise ValueError("secret_key required")
    if not callable(test_fn):
        raise TypeError("secret test_fn must be callable")
    with _LOCK:
        _SECRET_TESTERS[key] = {"plugin_id": pid, "test_fn": test_fn}


def run_secret_test(
    plugin_id: str,
    secret_key: str,
    api_key: str = "",
) -> dict[str, Any]:
    """Call a registered secret tester. Empty api_key → use the saved credential."""
    from backend.agent.secrets import get_key
    from backend.uefn_plugins.store import (
        load_plugin_manifest,
        normalize_plugin_id,
    )

    try:
        pid = normalize_plugin_id(plugin_id)
    except ValueError as exc:
        return {"ok": False, "detail": str(exc)}
    key = str(secret_key or "").strip()
    if not key:
        return {"ok": False, "detail": "secret key required"}
    manifest = load_plugin_manifest(pid)
    if manifest is None:
        return {"ok": False, "detail": f"UEFN plugin not found: {pid}"}
    raw = manifest.get("secret_keys") if isinstance(manifest.get("secret_keys"), list) else []
    allowed = {str(k).strip() for k in raw if str(k).strip()}
    if key not in allowed:
        return {"ok": False, "detail": f"Key {key!r} is not declared in {pid} secret_keys"}
    with _LOCK:
        row = _SECRET_TESTERS.get(key)
    if not row or row.get("plugin_id") != pid or not callable(row.get("test_fn")):
        return {"ok": False, "detail": "No API key test registered for this plugin"}
    candidate = str(api_key or "").strip() or (get_key(key) or "").strip()
    if not candidate:
        return {"ok": False, "detail": "Paste an API key first"}
    try:
        result = row["test_fn"](candidate)
    except Exception as exc:  # noqa: BLE001 — never raise across the JS bridge
        return {"ok": False, "detail": f"Test failed: {exc}"}
    if isinstance(result, dict):
        ok = bool(result.get("ok"))
        detail = str(result.get("detail") or ("OK" if ok else "Test failed"))
        return {"ok": ok, "detail": detail}
    return {"ok": bool(result), "detail": "OK" if result else "Test failed"}


# Names from the rest of the host, bound after every definition above so the host
# modules can import each other in any order.
from backend.uefn_plugins.plugin_host.lifecycle import is_plugin_enabled  # noqa: E402
from backend.uefn_plugins.plugin_host.state import (  # noqa: E402
    _CONNECTION_CACHE,
    _CONNECTION_CACHE_TTL_S,
    _CONNECTION_PROBES,
    _LOCK,
    _MEDIA_GENERATORS,
    _PEOPLE_CACHE,
    _PEOPLE_PROVIDERS,
    _SECRET_TESTERS,
    _TTS_SYNTHESIZERS,
    _TTS_VOICE_LISTERS,
    MEDIA_KINDS,
)
