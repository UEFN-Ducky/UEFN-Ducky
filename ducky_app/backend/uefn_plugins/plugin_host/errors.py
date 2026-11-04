"""Why plugins didn't load: the last load failure per plugin, kept for Settings and tool errors."""

from __future__ import annotations


def plugin_load_failures() -> dict[str, str]:
    """{plugin label: error} for plugins whose last load in this process failed."""
    with _LOCK:
        failures = dict(_LOAD_FAILURES)
    return {_plugin_label(pid): err for pid, err in failures.items()}


def _plugin_label(pid: str) -> str:
    try:
        from backend.uefn_plugins.store import load_plugin_manifest

        return str((load_plugin_manifest(pid) or {}).get("label") or pid)
    except Exception:
        return pid


def _record_plugin_load_error(pid: str, exc: BaseException | str) -> None:
    """Persist last register failure so Settings can explain 'Backend not loaded'.

    A plain string is a notice for the person and is recorded as written.
    """
    from backend.util.privacy import scrub

    # Shown in Settings and kept for 30 days, so no folders, emails or keys in it.
    message = scrub(exc if isinstance(exc, str) else f"{type(exc).__name__}: {exc}")
    _LOAD_FAILURES[pid] = message
    try:
        from backend.store.switch import use_db

        if use_db("events"):
            import time as _t

            from backend.store.repos import events as _ev

            _ev.insert("plugin_load_error", ts=_t.time(), source=pid, message=message,
                       payload={"plugin_id": pid, "error": message})
            _ev.trim("plugin_load_error", older_than=_t.time() - 30 * 86400, keep=_LOAD_ERROR_LOG_KEEP)
            return
    except Exception:
        pass
    try:
        from frontend.settings import default_app_data_dir

        path = default_app_data_dir() / "uefn_plugin_load_errors.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        import json
        import time

        line = json.dumps(
            {"ts": time.time(), "plugin_id": pid, "error": message},
            ensure_ascii=False,
        )
        # Repair retries append the same failure every ensure_plugins_loaded() —
        # unrotated this reached 13.5k lines / 1.9MB. Keep the newest tail only.
        if path.is_file() and path.stat().st_size > _LOAD_ERROR_LOG_MAX_BYTES:
            kept = path.read_text(encoding="utf-8").splitlines()[-_LOAD_ERROR_LOG_KEEP:]
            path.write_text("\n".join(kept) + "\n", encoding="utf-8")
        with path.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except Exception:
        pass


def tried_and_not_loaded() -> set[str]:
    """Plugins this process tried to load that are not loaded here: register() failed, or
    the plugin is paused (access to its audience lost). Their tools will not appear here on
    their own."""
    return (set(_LOAD_FAILURES) | set(_PAUSED_PRIVATE_PLUGINS)) - set(_REGISTERED)


# Names from the rest of the host, bound after every definition above so the host
# modules can import each other in any order.
from backend.uefn_plugins.plugin_host.lifecycle import _PAUSED_PRIVATE_PLUGINS  # noqa: E402
from backend.uefn_plugins.plugin_host.state import (  # noqa: E402
    _LOAD_ERROR_LOG_KEEP,
    _LOAD_ERROR_LOG_MAX_BYTES,
    _LOAD_FAILURES,
    _LOCK,
    _REGISTERED,
)
