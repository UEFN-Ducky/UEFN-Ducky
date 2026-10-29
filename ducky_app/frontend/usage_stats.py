"""Anonymous usage stats: counts of what the app does and what fails.

Every event is checked against :mod:`frontend.usage_events` (names and enum or
bucket props only), queued in ducky.db, and sent in batches to the UEFN Ducky
Store's ``usage`` collect route: every 5 minutes, or sooner once 50 rows wait.
Signed out, the batch carries only a random install id made on first run; signed
in, the account's token goes with it and the server attaches the account.

The ``usage_stats`` setting (on by default) is the one switch: off means nothing
is queued, nothing is sent and the queue is emptied. Every public function here
swallows its own errors: stats never break the app.
"""

from __future__ import annotations

import json
import secrets
import sys
import threading
import time
from typing import Any, Callable

from frontend import usage_events as ev

FLUSH_EVERY_S = 300.0
FLUSH_AT_ROWS = 50
HEARTBEAT_S = 90.0
BATCH_MAX = 200
QUEUE_MAX_ROWS = 2000
QUEUE_MAX_AGE_S = 7 * 86400
CLOSE_FLUSH_WAIT_S = 1.0
PERF_SAMPLE_EVERY_S = 86400.0
ENABLED_CACHE_S = 15.0

KIND = "usage_queue"
META_INSTALL = "usage.install_id"
META_INSTALL_AT = "usage.install_created"
META_LAST_VERSION = "usage.last_version"
META_FIRST_MESSAGE = "usage.first_message_done"
META_PERF_AT = "usage.perf_sampled_at"
# "1" while the app runs; still "1" at the next start means the last run never reached app_closing.
META_RUNNING = "usage.running"

_lock = threading.Lock()
_thread: threading.Thread | None = None
_stop = threading.Event()
_wake = threading.Event()
_mem_queue: list[dict[str, Any]] = []
_enabled_cache: tuple[float, bool] | None = None
_run_meta: dict[str, dict[str, Any]] = {}
_conv_run: dict[str, str] = {}
_opened_at = 0.0
_boot_total_ms: float | None = None
_install_fresh = False
_last_uefn_mode: str | None = None
# Test seam: the sender; returns True when the batch landed.
send_override: Callable[[dict[str, Any]], bool] | None = None

# Setup, the web installer and the uninstaller (release/installer/host/Usage.cs) read this
# PC's id and the switch here, so one PC counts once from download to uninstall. Setup
# makes the id before the app's first start; the app takes it over and keeps it current.
REG_KEY = r"Software\UEFN Ducky"
# Test seam: a dict standing in for that key (None = the real key, frozen app only).
registry_override: dict[str, Any] | None = None


# ----------------------------------------------------------------------------- storage

def _use_db() -> bool:
    try:
        from backend.store.switch import use_db

        return use_db("events")
    except Exception:
        return False


def _meta_get(key: str) -> str | None:
    try:
        from backend.store.repos.kv import meta_get

        return meta_get(key)
    except Exception:
        return None


def _meta_set(key: str, value: str) -> None:
    try:
        from backend.store.repos.kv import meta_set

        meta_set(key, value)
    except Exception:
        pass


def _registry_on() -> bool:
    """Only the installed app touches HKCU: never a source run, a test or a sandboxed build."""
    if registry_override is not None:
        return True
    if sys.platform != "win32" or not getattr(sys, "frozen", False):
        return False
    try:
        from frontend.ide_apply import auto_apply_skip_reason

        return not auto_apply_skip_reason()
    except Exception:
        return False


def _reg_read() -> dict[str, Any]:
    if registry_override is not None:
        return dict(registry_override)
    if not _registry_on():
        return {}
    import winreg

    out: dict[str, Any] = {}
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, REG_KEY) as key:
            for name in ("InstallId", "InstallSince", "UsageStats"):
                try:
                    out[name] = winreg.QueryValueEx(key, name)[0]
                except OSError:
                    pass
    except OSError:
        pass
    return out


def _reg_write(values: dict[str, Any]) -> None:
    if registry_override is not None:
        registry_override.update(values)
        return
    if not _registry_on():
        return
    import winreg

    try:
        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, REG_KEY, 0, winreg.KEY_SET_VALUE) as key:
            for name, value in values.items():
                if isinstance(value, int):
                    winreg.SetValueEx(key, name, 0, winreg.REG_DWORD, value)
                else:
                    winreg.SetValueEx(key, name, 0, winreg.REG_SZ, str(value))
    except OSError:
        pass


def _setup_id() -> tuple[str, float] | None:
    """The id (and its date) Setup made on this PC before the app's first start."""
    reg = _reg_read()
    rid = str(reg.get("InstallId") or "").strip().lower()
    if len(rid) != 32 or any(c not in "0123456789abcdef" for c in rid):
        return None
    try:
        since = float(reg.get("InstallSince") or 0)
    except (TypeError, ValueError):
        since = 0.0
    return rid, since if 0 < since <= time.time() else time.time()


def install_id() -> str:
    """The random install id, made on first use (or Setup's). Never derived from the machine."""
    global _install_fresh
    with _lock:
        existing = _meta_get(META_INSTALL)
        if existing and len(existing) == 32:
            return existing
        from_setup = _setup_id()
        fresh, since = from_setup or (secrets.token_hex(16), time.time())
        _meta_set(META_INSTALL, fresh)
        _meta_set(META_INSTALL_AT, str(since))
        _install_fresh = True
        return fresh


def _mirror(on: bool) -> None:
    """Keep Setup and the uninstaller in step: the switch, and (on) this PC's id."""
    try:
        if not _registry_on():
            return
        values: dict[str, Any] = {"UsageStats": 1 if on else 0}
        if on:
            values["InstallId"] = install_id()
            try:
                values["InstallSince"] = str(int(float(_meta_get(META_INSTALL_AT) or time.time())))
            except (TypeError, ValueError):
                pass
        _reg_write(values)
    except Exception:
        pass


def install_age_s() -> float:
    try:
        return max(0.0, time.time() - float(_meta_get(META_INSTALL_AT) or time.time()))
    except (TypeError, ValueError):
        return 0.0


def reset_install_id() -> None:
    """Forget the install id (the next start counts as a new install)."""
    _meta_set(META_INSTALL, "")
    _meta_set(META_INSTALL_AT, "")


# ----------------------------------------------------------------------------- the switch

def enabled() -> bool:
    global _enabled_cache
    now = time.monotonic()
    cached = _enabled_cache
    if cached is not None and now - cached[0] < ENABLED_CACHE_S:
        return cached[1]
    try:
        from frontend.settings import PanelSettings

        on = bool(getattr(PanelSettings.load(), "usage_stats", True))
    except Exception:
        on = False
    _enabled_cache = (now, on)
    return on


def set_enabled(flag: bool) -> None:
    """Called when the setting changes: off empties the queue at once."""
    global _enabled_cache
    _enabled_cache = (time.monotonic(), bool(flag))
    if not flag:
        clear_queue()
    _mirror(bool(flag))


# ----------------------------------------------------------------------------- queue

def _enqueue(row: dict[str, Any]) -> int:
    if _use_db():
        try:
            from backend.store.repos import events

            events.insert(KIND, ts=float(row["ts"]), source=str(row["name"]), payload=row)
            n = events.count(KIND)
            if n > QUEUE_MAX_ROWS:
                events.trim(KIND, older_than=time.time() - QUEUE_MAX_AGE_S, keep=QUEUE_MAX_ROWS)
                n = QUEUE_MAX_ROWS
            return n
        except Exception:
            return 0
    with _lock:
        _mem_queue.append(row)
        if len(_mem_queue) > QUEUE_MAX_ROWS:
            del _mem_queue[: len(_mem_queue) - QUEUE_MAX_ROWS]
        return len(_mem_queue)


def queued() -> int:
    if _use_db():
        try:
            from backend.store.repos import events

            return events.count(KIND)
        except Exception:
            return 0
    with _lock:
        return len(_mem_queue)


def clear_queue() -> None:
    if _use_db():
        try:
            from backend.store.repos import events

            events.clear(KIND)
        except Exception:
            pass
    with _lock:
        _mem_queue.clear()


def _take(limit: int) -> tuple[list[dict[str, Any]], list[int]]:
    """Oldest queued events and the ids to delete once they are sent."""
    if _use_db():
        try:
            from backend.store.repos import events

            rows = events.oldest(KIND, limit=limit)
        except Exception:
            return [], []
        out: list[dict[str, Any]] = []
        ids: list[int] = []
        for r in rows:
            ids.append(int(r["id"]))
            p = r.get("payload")
            if isinstance(p, dict) and p.get("name"):
                out.append({"name": p["name"], "props": p.get("props") or {}, "ts": float(p.get("ts") or r["ts"])})
        return out, ids
    with _lock:
        batch = list(_mem_queue[:limit])
    return batch, list(range(len(batch)))


def _drop(ids: list[int]) -> None:
    if _use_db():
        try:
            from backend.store.repos import events

            events.delete_ids(ids)
        except Exception:
            pass
        return
    with _lock:
        del _mem_queue[: len(ids)]


def track(name: str, **props: Any) -> bool:
    """Queue one event. False when stats are off or the event is not on the list."""
    try:
        if not enabled():
            return False
        cleaned = ev.validate(name, props)
        if cleaned is None:
            return False
        n = _enqueue({"name": name, "props": cleaned, "ts": time.time()})
        if n >= FLUSH_AT_ROWS:
            _wake.set()
        return True
    except Exception:
        return False


def ui_track(name: str, props: dict[str, Any] | None) -> bool:
    """Events the panel UI reports (tabs, setup steps); the rest stay backend-only."""
    if name not in ev.UI_EVENTS:
        return False
    return track(name, **(props if isinstance(props, dict) else {}))


# ----------------------------------------------------------------------------- sending

def _send(body: dict[str, Any]) -> bool:
    if send_override is not None:
        return bool(send_override(body))
    try:
        from frontend.duckyos_account import _store_collect

        # The Store answers a refused batch with 200 and {"error": ...}: keep it queued.
        return not _store_collect("usage", body, allow_anonymous=True, timeout=20.0).get("error")
    except Exception:
        return False


def flush() -> bool:
    """Send the queue in batches; stops at the first batch that does not land."""
    if not enabled():
        return False
    try:
        from frontend import __version__
    except Exception:
        __version__ = "0.0.0"
    while True:
        rows, ids = _take(BATCH_MAX)
        if not rows:
            return True
        body = {"installId": install_id(), "appVersion": __version__, "events": rows}
        if not _send(body):
            return False
        _drop(ids)
        if len(rows) < BATCH_MAX:
            return True


# ----------------------------------------------------------------------------- the thread

def _loop() -> None:
    last_flush = time.monotonic()
    last_beat = time.monotonic()
    while not _stop.is_set():
        _wake.wait(5.0)
        _wake.clear()
        if _stop.is_set():
            break
        now = time.monotonic()
        try:
            if now - last_beat >= HEARTBEAT_S:
                last_beat = now
                track("app.heartbeat", uefn_up=_last_uefn_mode == "online")
                _maybe_perf_sample()
            due = now - last_flush >= FLUSH_EVERY_S or queued() >= FLUSH_AT_ROWS
            if due and enabled():
                last_flush = now
                flush()
        except Exception:
            pass


def start() -> None:
    global _thread
    with _lock:
        if _thread is not None and _thread.is_alive():
            return
        _stop.clear()
        _thread = threading.Thread(target=_loop, name="usage-stats", daemon=True)
        _thread.start()


def stop() -> None:
    _stop.set()
    _wake.set()


# ----------------------------------------------------------------------------- lifecycle

def _os_label() -> str:
    try:
        if sys.platform != "win32":
            return "other"
        return "win11" if sys.getwindowsversion().build >= 22000 else "win10"
    except Exception:
        return "other"


def app_opened(boot_total_ms: float | None = None) -> None:
    """The panel is up: app.open, an update if the version changed, then the thread."""
    global _opened_at, _boot_total_ms
    try:
        from frontend import error_log

        error_log.install_crash_hooks()
    except Exception:
        pass
    try:
        _opened_at = time.monotonic()
        _boot_total_ms = boot_total_ms
        unclean = _meta_get(META_RUNNING) == "1"
        _meta_set(META_RUNNING, "1")
        if not enabled():
            _mirror(False)
            return
        from frontend import __version__

        install_id()
        _mirror(True)
        last = _meta_get(META_LAST_VERSION) or ""
        if unclean:
            track("error.code", code="app_unclean_exit", where="ui", kind="crash", version=last)
        if last and last != __version__:
            track("update.installed", **{"from": last, "to": __version__})
        if last != __version__:
            _meta_set(META_LAST_VERSION, __version__)
        track("app.open", version=__version__, os=_os_label(), install_age_s=install_age_s())
        start()
    except Exception:
        pass


_update_check_sent = False


def update_checked(status: str, channel: str, pack: bool) -> None:
    """The session's first update check: why a PC stays on an old build (portable copy,
    paused feed, feed error, update waiting). A quarter of launches ran builds a week
    old (Oct 26 2026) and nothing said which."""
    global _update_check_sent
    if _update_check_sent:
        return
    _update_check_sent = True
    track("update.check", status=status, channel=channel, pack=pack)


def app_closing() -> None:
    """Quit: app.close, then a short bounded flush so the session's rows land."""
    try:
        _meta_set(META_RUNNING, "0")
        if not enabled():
            return
        uptime = time.monotonic() - _opened_at if _opened_at else 0.0
        track("app.close", uptime_s=uptime)
        stop()
        t = threading.Thread(target=flush, name="usage-stats-final", daemon=True)
        t.start()
        t.join(CLOSE_FLUSH_WAIT_S)
    except Exception:
        pass


def _uefn_window_open() -> bool | None:
    """Whether a UEFN window is open now; None when the windows can't be read."""
    try:
        from backend.tools.core.uefn_modal import _enum_uefn_windows

        return bool(_enum_uefn_windows())
    except Exception:
        return None


def _lost_reason(mode: str) -> str:
    """Why an online link went away. All of them said "lost" (31 of 71 on Oct 27 2026), so
    closing UEFN read the same as the editor hanging."""
    if mode == "wedged":
        return "timeout"
    return "not_running" if _uefn_window_open() is False else "lost"


def uefn_mode(mode: str) -> None:
    """The shell's connection mode changed (online / offline / wedged)."""
    global _last_uefn_mode
    try:
        prev = _last_uefn_mode
        _last_uefn_mode = mode
        if mode == "online" and prev != "online":
            track("uefn.connect", ok=True)
        elif prev == "online" and mode != "online":
            track("uefn.connect", ok=False, reason=_lost_reason(mode))
    except Exception:
        pass


# ----------------------------------------------------------------------------- chats and tools

def run_begin(run_id: str, conv_id: str, *, coding_agent: str = "", provider: str = "", model: str = "") -> None:
    try:
        if not enabled():
            return
        with _lock:
            _run_meta[run_id] = {
                "provider": ev.provider_of(coding_agent, provider),
                "model_family": ev.model_family(model, provider),
                "started": time.monotonic(),
                "tools": 0,
            }
            _conv_run[conv_id] = run_id
            if len(_run_meta) > 500:
                for stale in list(_run_meta)[:-200]:
                    _run_meta.pop(stale, None)
        if not _meta_get(META_FIRST_MESSAGE):
            _meta_set(META_FIRST_MESSAGE, "1")
            track("setup.first_message", within="first_session" if _install_fresh else "later")
    except Exception:
        pass


_LIMIT_WORDS = ("session limit", "usage limit", "rate limit", "rate_limit", "hit your limit", "quota",
                "credit balance", "overloaded")


def _turn_outcome(reason: str, detail: str = "") -> str:
    """A run's end reason as a chat.turn outcome. A plan limit, a sign-in prompt or a closed app
    is not a failed turn: all three counted as "error" with 0 tool calls (Oct 26 2026)."""
    if reason in ("error", "needs_login") and any(w in detail.lower() for w in _LIMIT_WORDS):
        return "limit"
    return {"done": "ok", "error": "error", "timeout": "timeout", "cancelled": "stopped", "stopped": "stopped",
            "needs_login": "login", "interrupted": "interrupted"}.get(reason, "other")


def run_end(run_id: str, reason: str, detail: str = "") -> None:
    try:
        with _lock:
            meta = _run_meta.pop(run_id, None)
            for conv, rid in list(_conv_run.items()):
                if rid == run_id:
                    _conv_run.pop(conv, None)
        if meta is None:
            return
        outcome = _turn_outcome(reason, detail)
        # Why a turn failed, as one ERROR_KINDS word (Oct 27 2026: Codex 0 -> 13 errors, 7 within
        # 10 s, and nothing said why). The detail itself never leaves.
        why = {"kind": ev.kind_of(detail)} if outcome == "error" else {}
        track(
            "chat.turn",
            provider=meta["provider"],
            model_family=meta["model_family"],
            outcome=outcome,
            duration_s=time.monotonic() - float(meta["started"]),
            tool_calls_n=int(meta["tools"]),
            **why,
        )
    except Exception:
        pass


def turn_tool(run_id: str) -> None:
    """One more tool call in a Claude Code / Codex / Cursor turn. Their calls are counted as
    tool.call by the shared tool server, a process with no turns, so every such chat.turn
    said "0 tool calls" (Oct 22 2026: 86% of turns, 20-minute ones included)."""
    try:
        with _lock:
            meta = _run_meta.get(run_id or "")
            if meta is not None:
                meta["tools"] = int(meta["tools"]) + 1
    except Exception:
        pass


def tool_done(name: str, *, ok: bool, duration_ms: float, conv_id: str = "", args: dict[str, Any] | None = None) -> None:
    try:
        if not enabled():
            return
        with _lock:
            rid = _conv_run.get(conv_id or "")
            meta = _run_meta.get(rid or "")
            if meta is not None:
                meta["tools"] = int(meta["tools"]) + 1
        track("tool.call", tool=name, ok=ok, duration_s=float(duration_ms) / 1000.0)
        if name in ("skill_read_subskill", "uefn_skill", "skill_search") and isinstance(args, dict):
            pack = args.get("pack_id") or args.get("pack") or args.get("skill") or args.get("name")
            if pack:
                track("skill.used", skill=pack)
    except Exception:
        pass


def stuck(coding_agent: str, seconds: float) -> None:
    track("agent.stuck", provider=ev.provider_of(coding_agent), after_s=seconds)


# ----------------------------------------------------------------------------- reliability

def _app_version() -> str:
    try:
        from frontend import __version__

        return __version__
    except Exception:
        return ""


def error(source: str, message: str = "", at: str = "", kind: str = "") -> None:
    """An error log line: its source tag becomes the code and its message one ERROR_KINDS word;
    the message itself never leaves. ``at``: the Ducky function it came from (a slug)."""
    track("error.code", code=ev.code_of(source), where=ev.where_of(at or source),
          kind=kind or ev.kind_of(message), version=_app_version(), at=at)


def agent_crash(provider: str) -> None:
    track("error.code", code="agent_crash_" + (ev.slug(provider) or "other"), where="agent",
          kind="crash", version=_app_version())


def ui_crash(code: str) -> None:
    track("ui.crash", code=ev.code_of(code or "boundary"))


def workflow_done(*, ok: bool, stopped: bool, nodes: int) -> None:
    track("workflow.run", outcome="stopped" if stopped else "ok" if ok else "error", nodes_n=nodes)


def plugin_installed(slug: str) -> None:
    track("plugin.install", plugin_id=slug)


def plugin_removed(plugin_id: str) -> None:
    track("plugin.remove", plugin_id=plugin_id)


def login(agent_id: str, *, ok: bool, reason: str = "other") -> None:
    track("agent.login", provider=ev.provider_of(agent_id), ok=ok, reason=reason)


def popup(kind: str) -> None:
    track("uefn.popup_watchdog", kind=kind)


def _maybe_perf_sample() -> None:
    try:
        last = float(_meta_get(META_PERF_AT) or 0.0)
    except (TypeError, ValueError):
        last = 0.0
    if time.time() - last < PERF_SAMPLE_EVERY_S:
        return
    _meta_set(META_PERF_AT, str(time.time()))
    # Turns running now: an "idle" CPU sample taken mid-turn is work, not idle (~28% over 5%, Oct 26 2026).
    with _lock:
        props: dict[str, Any] = {"agents_running_n": len(_run_meta)}
    if _boot_total_ms:
        props["startup_s"] = float(_boot_total_ms) / 1000.0
    try:
        import psutil

        proc = psutil.Process()
        props["memory_mb"] = proc.memory_info().rss / (1024 * 1024)
        props["idle_cpu_pct"] = proc.cpu_percent(interval=1.0)
    except Exception:
        pass
    if props:
        track("perf.sample", **props)


def debug_state() -> dict[str, Any]:
    """For tests and the support dump: no event content, just counts."""
    return {
        "enabled": enabled(),
        "queued": queued(),
        "thread_alive": bool(_thread is not None and _thread.is_alive()),
        "runs_open": len(_run_meta),
        "install_id_set": bool(_meta_get(META_INSTALL)),
    }


__all__ = [
    "track", "ui_track", "flush", "install_id", "enabled", "set_enabled", "app_opened", "app_closing",
    "uefn_mode", "run_begin", "run_end", "tool_done", "stuck", "error", "agent_crash", "ui_crash",
    "workflow_done", "plugin_installed", "plugin_removed", "login", "popup", "queued", "clear_queue",
    "debug_state", "reset_install_id", "json",
]
