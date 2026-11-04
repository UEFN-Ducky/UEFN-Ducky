"""Gateways plugins register: LLM providers, coding agents, and IDE MCP / skills Apply targets."""

from __future__ import annotations

import threading
from typing import Any, Callable

# Deferred register_ide_hookup(auto_apply=True) worker — never on the load thread.
_IDE_APPLY_THREAD: threading.Thread | None = None


def register_llm_provider_factory(
    plugin_id: str,
    provider_id: str,
    factory: Any,
    *,
    fetch_models: Any = None,
    test_key_model: str = "",
    test_key: Any = None,
    tool_schema: str = "",
    key_optional: bool = False,
    normalize_secret: Any = None,
    **extra: Any,
) -> None:
    """Host-side register for ``api.register_llm_provider``.

    Optional vendor hooks (no host if-vendor):
    - tool_schema: \"openai\" | \"anthropic\" | \"gemini\"
    - key_optional: True for URL gateways (Ollama)
    - normalize_secret: ``(raw) -> str`` before test/fetch
    - fetch_usage: ``(api_key, *, model=\"\") -> {windows, notice?}`` live quota sliders
    - read_cached_models: ``() -> list[ModelInfo]`` disk only, no Node or network
    """
    from backend.uefn_plugins.store import normalize_plugin_id

    pid = normalize_plugin_id(plugin_id)
    provid = str(provider_id or "").strip().lower()
    if not provid:
        raise ValueError("provider id required")
    if not callable(factory):
        raise TypeError("LLM provider factory must be callable")
    row: dict[str, Any] = {
        "plugin_id": pid,
        "factory": factory,
        "fetch_models": fetch_models if callable(fetch_models) else None,
        "test_key_model": str(test_key_model or "").strip(),
        "test_key": test_key if callable(test_key) else None,
        "tool_schema": str(tool_schema or "").strip().lower(),
        "key_optional": bool(key_optional),
        "normalize_secret": normalize_secret if callable(normalize_secret) else None,
    }
    for k, v in extra.items():
        if v is not None:
            row[k] = v
    with _LOCK:
        _LLM_PROVIDER_FACTORIES[provid] = row


def register_coding_agent_factory(
    plugin_id: str,
    agent_id: str,
    factory: Any,
    *,
    complete_one_shot: Any = None,
    test_key: Any = None,
    resolve_api_fallback: Any = None,
    aliases: list[str] | tuple[str, ...] | None = None,
    skills_dir: Any = None,
    native_skills: bool = False,
    thinking_env: Any = None,
    normalize_model: Any = None,
    before_launch: Any = None,
    on_needs_login: Any = None,
    settings_defaults: dict[str, Any] | None = None,
    install_help: str = "",
    supports_batch_complete: bool = False,
    **extra: Any,
) -> None:
    """Host-side register for ``api.register_coding_agent``.

    Vendor behavior lives on the registration record — not ``if id == …`` in core.
    """
    from backend.uefn_plugins.store import normalize_plugin_id

    pid = normalize_plugin_id(plugin_id)
    aid = str(agent_id or "").strip().lower().replace("-", "_")
    if not aid:
        raise ValueError("coding agent id required")
    if not callable(factory):
        raise TypeError("coding agent factory must be callable")
    alias_list = [
        str(a).strip().lower().replace("-", "_")
        for a in (aliases or ())
        if str(a).strip()
    ]
    row: dict[str, Any] = {
        "plugin_id": pid,
        "factory": factory,
        "complete_one_shot": complete_one_shot if callable(complete_one_shot) else None,
        "test_key": test_key if callable(test_key) else None,
        "resolve_api_fallback": resolve_api_fallback if callable(resolve_api_fallback) else None,
        "aliases": alias_list,
        "skills_dir": skills_dir,
        "native_skills": bool(native_skills),
        "thinking_env": thinking_env if callable(thinking_env) else None,
        "normalize_model": normalize_model if callable(normalize_model) else None,
        "before_launch": before_launch if callable(before_launch) else None,
        "on_needs_login": on_needs_login if callable(on_needs_login) else None,
        "settings_defaults": dict(settings_defaults or {}),
        "install_help": str(install_help or ""),
        "supports_batch_complete": bool(supports_batch_complete),
    }
    for k, v in extra.items():
        if v is not None:
            row[k] = v
    with _LOCK:
        _CODING_AGENT_FACTORIES[aid] = row


def get_llm_provider_registration(provider_id: str) -> dict[str, Any] | None:
    """Return registration for a provider id (may be disabled — caller gates).

    None while its plugin hides it (``visible``), so its models and meter never load."""
    provid = str(provider_id or "").strip().lower()
    if not provid or not provider_visible(provid):
        return None
    with _LOCK:
        row = _LLM_PROVIDER_FACTORIES.get(provid)
        return dict(row) if row else None


# Gateway options a plugin passes to register_llm_provider (core never names a gateway):
# - visible: () -> bool, False hides the gateway everywhere (an account without access)
# - fallback_model: the model a chat with no usable model takes, while visible
# - remote: True when the model runs on a server and cannot read this PC
# - billing: how its turns are paid, e.g. "account"
# - model_ids: its own model ids, so a bare id finds its gateway
# - model_aliases: {old id: current id}, so chats saved with an old id keep working
def provider_option(provider_id: Any, key: str, default: Any = None) -> Any:
    provid = str(provider_id or "").strip().lower()
    with _LOCK:
        row = _LLM_PROVIDER_FACTORIES.get(provid)
        return row.get(key, default) if row else default


def provider_visible(provider_id: Any) -> bool:
    check = provider_option(provider_id, "visible")
    if not callable(check):
        return True
    try:
        return bool(check())
    except Exception:
        return False


def registered_providers() -> list[str]:
    with _LOCK:
        return list(_LLM_PROVIDER_FACTORIES)


def provider_for_model(model: Any) -> str:
    """The gateway whose model_ids or model_aliases hold this bare model id ("" when none)."""
    mid = str(model or "").strip().lower()
    if not mid:
        return ""
    with _LOCK:
        rows = list(_LLM_PROVIDER_FACTORIES.items())
    for provid, row in rows:
        owned = {str(m).strip().lower() for m in row.get("model_ids") or ()}
        owned |= {str(m).strip().lower() for m in dict(row.get("model_aliases") or {})}
        if mid in owned:
            return provid
    return ""


def current_model_id(provider_id: Any, model: Any) -> str:
    """The id a gateway uses now for ``model`` (its model_aliases), else ``model`` as given."""
    mid = str(model or "").strip()
    aliases = provider_option(provider_id, "model_aliases") or {}
    return str(dict(aliases).get(mid.lower()) or dict(aliases).get(mid) or mid)


def _picked_account_key(provider_id: str, conv_id: str = "") -> str:
    """The key of the account to use now (the chat's or the default, or the next one while
    it's full); "" when unset."""
    try:
        from backend.agent import accounts

        picked = accounts.pick_account(provider_id, conv_id=conv_id)
        return str(accounts.account_key(provider_id, picked) or "").strip()
    except Exception:
        return ""


def resolve_gateway_credential(
    provider_id: str, raw: str = "", *, row: dict[str, Any] | None = None, conv_id: str = ""
) -> str:
    """Saved key, then plugin ``normalize_secret``, then URL default (Ollama).

    Hosted providers like UEFN Ducky keep the device key in ``duckyos_account``
    and resolve it here — they must not require a pasted LLM secret.

    ``row``: the gateway's ``llm_providers`` row when the caller already holds one, so
    this does not rebuild every plugin contribution just to find it.
    ``conv_id``: the chat asking, so a chat on its own account gets that account's key.
    """
    from backend.agent.secrets import get_key

    name = str(provider_id or "").strip().lower()
    if not name:
        return ""
    key = str(raw or "").strip() or _picked_account_key(name, conv_id) or str(get_key(name) or "").strip()
    reg = get_llm_provider_registration(name) or {}
    norm = reg.get("normalize_secret")
    if callable(norm):
        try:
            key = str(norm(key) or "").strip()
        except Exception:
            pass
    if key:
        return key
    if row is None:
        for candidate in get_contributions().get("llm_providers") or []:
            if not isinstance(candidate, dict):
                continue
            if str(candidate.get("id") or "").strip().lower() == name:
                row = candidate
                break
    kind = str((row or {}).get("kind") or "").strip().lower()
    if kind == "url" or (reg.get("key_optional") and kind != "secret"):
        return str((row or {}).get("default_url") or "").strip() or "http://localhost:11434"
    return ""


def get_coding_agent_registration(agent_id: str) -> dict[str, Any] | None:
    aid = str(agent_id or "").strip().lower().replace("-", "_")
    if not aid:
        return None
    with _LOCK:
        row = _CODING_AGENT_FACTORIES.get(aid)
        return dict(row) if row else None


def registered_llm_provider_ids() -> tuple[str, ...]:
    with _LOCK:
        return tuple(sorted(_LLM_PROVIDER_FACTORIES.keys()))


def registered_coding_agent_ids() -> tuple[str, ...]:
    with _LOCK:
        return tuple(sorted(_CODING_AGENT_FACTORIES.keys()))


def register_ide_hookup_factory(plugin_id: str, kind: str, *, label: str = "") -> None:
    """Bind an IDE MCP/skills Apply target to a Store gateway plugin."""
    from backend.uefn_plugins.store import normalize_plugin_id

    pid = normalize_plugin_id(plugin_id)
    key = (kind or "").strip().lower().replace("-", "_")
    if not key or not key.replace("_", "").isalnum():
        raise ValueError(f"unsupported ide hookup kind: {kind!r}")
    with _LOCK:
        _IDE_HOOKUPS[key] = {
            "plugin_id": pid,
            "label": (label or key).strip() or key,
        }


def get_ide_hookup(kind: str) -> dict[str, Any] | None:
    key = (kind or "").strip().lower()
    with _LOCK:
        row = _IDE_HOOKUPS.get(key)
        if not row:
            return None
        out = dict(row)
    pid = str(out.get("plugin_id") or "")
    if pid and not is_plugin_enabled(pid):
        return None
    return out


def registered_ide_hookup_kinds() -> tuple[str, ...]:
    with _LOCK:
        return tuple(sorted(_IDE_HOOKUPS.keys()))


def _ide_auto_apply_worker() -> None:
    """Drain queued IDE applies once the load pass is done. One thread — applies
    share skills roots, so they must not run concurrently."""
    global _IDE_APPLY_THREAD
    try:
        from frontend.ide_apply import apply_ide_bridge
        from frontend.ide_paths import IdeKind

        _LOAD_DONE.wait(timeout=180.0)  # let coding-agent skills_dir registrations land
        while True:
            with _IDE_APPLY_LOCK:
                if not _IDE_APPLY_PENDING:
                    # Clear under the same lock as the emptiness check, or a queue
                    # racing here would see a live thread and orphan its item.
                    _IDE_APPLY_THREAD = None
                    return
                kind, log = _IDE_APPLY_PENDING.pop(0)
            try:
                log(f"IDE auto-apply → {apply_ide_bridge(IdeKind(kind))}")
            except Exception as exc:  # noqa: BLE001 — IDE may be absent
                log(f"IDE auto-apply skipped ({kind}): {exc}")
    except Exception:  # noqa: BLE001 — never kill the app on a bad IDE import
        _log.exception("IDE auto-apply worker failed")
        with _IDE_APPLY_LOCK:
            _IDE_APPLY_THREAD = None
            _IDE_APPLY_PENDING.clear()


def _queue_ide_auto_apply(kind: str, log: Callable[[str], None]) -> None:
    key = (kind or "").strip().lower()
    if not key:
        return
    global _IDE_APPLY_THREAD
    with _IDE_APPLY_LOCK:
        if any(k == key for k, _ in _IDE_APPLY_PENDING):
            return
        _IDE_APPLY_PENDING.append((key, log))
        if _IDE_APPLY_THREAD is not None and _IDE_APPLY_THREAD.is_alive():
            return
        _IDE_APPLY_THREAD = threading.Thread(
            target=_ide_auto_apply_worker, daemon=True, name="uefn-ide-auto-apply"
        )
        _IDE_APPLY_THREAD.start()


# Names from the rest of the host, bound after every definition above so the host
# modules can import each other in any order.
from backend.uefn_plugins.plugin_host.lifecycle import is_plugin_enabled  # noqa: E402
from backend.uefn_plugins.plugin_host.state import (  # noqa: E402
    _CODING_AGENT_FACTORIES,
    _IDE_APPLY_LOCK,
    _IDE_APPLY_PENDING,
    _IDE_HOOKUPS,
    _LLM_PROVIDER_FACTORIES,
    _LOAD_DONE,
    _LOCK,
    _log,
)
from backend.uefn_plugins.plugin_host.ui_registry import get_contributions  # noqa: E402
