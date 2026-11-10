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
import re
import secrets
import sys
import threading
import time
from typing import Any, Callable

from frontend import usage_events as ev

# One batch an hour (and one a minute after start, so the PC shows up today, and one at
# close): every 5 min was ~12 Store calls an hour per PC. Identical rows go as one with n.
FLUSH_EVERY_S = 3600.0
FIRST_FLUSH_S = 60.0
FLUSH_AT_ROWS = 1000
HEARTBEAT_S = 90.0
# Distinct (name, props) rows per batch: the Store's MAX_EVENTS.
BATCH_MAX = 200
QUEUE_MAX_ROWS = 5000
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
# {"stage", "uptime_s"} of this run, rewritten every heartbeat: an unclean exit says when it died.
META_LAST_SEEN = "usage.last_seen"

_lock = threading.Lock()
_thread: threading.Thread | None = None
_stop = threading.Event()
_wake = threading.Event()
_mem_queue: list[dict[str, Any]] = []
_enabled_cache: tuple[float, bool] | None = None
_run_meta: dict[str, dict[str, Any]] = {}
_conv_run: dict[str, str] = {}
# (conv_id, tool) -> whether that chat's last call of that tool failed: tool.call after_fail.
_last_failed: dict[tuple[str, str], bool] = {}
_opened_at = 0.0
_boot_total_ms: float | None = None
_install_fresh = False
_last_uefn_mode: str | None = None
# Test seam: the sender; True/"ok" landed, "refused" or "refused:<reason>" the Store said no,
# False/"failed" no answer.
send_override: Callable[[dict[str, Any]], bool | str] | None = None

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
        gone = set(ids)
        _mem_queue[:] = [r for i, r in enumerate(_mem_queue) if i not in gone]


def _coalesce(rows: list[dict[str, Any]], ids: list[int]) -> list[tuple[dict[str, Any], list[int]]]:
    """Identical (name, props) rows as one wire row with n, oldest first; ts is its last,
    t0 its first (the Store reads sessions from those)."""
    groups: dict[str, tuple[dict[str, Any], list[int]]] = {}
    for row, rid in zip(rows, ids):
        key = row["name"] + json.dumps(row.get("props") or {}, sort_keys=True)
        got = groups.get(key)
        if got is None:
            groups[key] = ({"name": row["name"], "props": row.get("props") or {}, "ts": row["ts"], "t0": row["ts"], "n": 1}, [rid])
        else:
            got[0]["n"] += 1
            got[0]["ts"] = max(got[0]["ts"], row["ts"])
            got[0]["t0"] = min(got[0]["t0"], row["ts"])
            got[1].append(rid)
    return list(groups.values())


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

# A batch the Store refuses this many times in a row is dropped: one row it can never take
# (a key past its limit) once held a PC's whole queue for a day, signed-in status and all.
REFUSED_DROP_AFTER = 3
_refused_in_a_row = 0


_USAGE_ROUTE = "/api/v1/plugins/uefn-ducky-store/collect/usage"
# Reasons that mean the batch was too big, not bad: it is split in half and each half sent once.
_TOO_LARGE = frozenset({"too_big", "too_large", "http_413", "payload_too_large"})
# The Store's refusals in a few fixed words (anr9c4x6p1h8j): first match on the reply wins.
_REFUSAL_CLASSES = (("install_id", r"install.?id"), ("too_big", r"too (large|big)|payload|max_events|too many"),
                    ("rate", r"too soon|rate|429"), ("origin", r"origin|cors"))


def reason_token(error: str, status: int = 0) -> str:
    """The Store's error as one short word for usage_batch_refused: install_id, too_big, rate,
    origin or server_5xx when it is one of those, else the text before its first ":" (never
    the detail after it) as [a-z0-9_] cut to 24 chars, else http_<status>."""
    if status == 413:
        return "too_big"
    if status >= 500:
        return "server_5xx"
    low = str(error or "").lower()
    for word, pattern in _REFUSAL_CLASSES:
        if re.search(pattern, low):
            return word
    head = low.split(":", 1)[0].strip()
    word = re.sub(r"[^a-z0-9]+", "_", head).strip("_")[:24].strip("_")
    return word or (f"http_{status}" if status else "unknown")


def _send(body: dict[str, Any]) -> str:
    """ok | refused:<reason> (the Store answered no to this batch) | failed (no answer: try again)."""
    if send_override is not None:
        got = send_override(body)
        return got if isinstance(got, str) else "ok" if got else "failed"
    try:
        from frontend.duckyos_account import api_request

        status, parsed, _raw = api_request(
            "POST", _USAGE_ROUTE, body, prefer_bearer=True, allow_anonymous=True, timeout=20.0
        )
        status = int(status)
    except Exception:
        return "failed"
    error = ""
    if isinstance(parsed, dict):
        payload = parsed.get("payload")
        error = str((payload.get("error") if isinstance(payload, dict) else None) or parsed.get("error") or "")
    # 404: the site worker mid-swap; 408/429/5xx: busy. Sent again later.
    if status in (404, 408, 429) or status >= 500:
        return "failed"
    if not 200 <= status < 300:
        return f"refused:{reason_token(error, status)}"
    # The Store answers a refused batch with 200 and {"error": ...}.
    if not error:
        return "ok"
    # "too soon" (rate) and "try again:" (the Store's own read/write failed) are sent
    # again later; only a batch the Store can never take counts toward dropping it.
    if "too soon" in error or "try again" in error:
        return "failed"
    return f"refused:{reason_token(error)}"


def _report_refused(reason: str, version: str) -> None:
    track("error.code", code="usage_batch_refused", where="store", kind="bad_request", version=version,
          reason=reason or "unknown")


def _send_halves(body: dict[str, Any], batch: list[Any], version: str) -> None:
    """A batch refused as too large: each half sent once. Halves that land or are refused are
    dropped (a refusal is reported); a half with no answer stays queued."""
    mid = len(batch) // 2
    refused: list[str] = []
    for half in (batch[:mid], batch[mid:]):
        got, _, why = _send({**body, "events": [g[0] for g in half]}).partition(":")
        if got == "ok":
            _drop([i for g in half for i in g[1]])
        elif got == "refused":
            _drop([i for g in half for i in g[1]])
            refused.append(why)
    if refused:
        _report_refused(refused[0], version)


def flush() -> bool:
    """Send one batch: the queue merged into at most BATCH_MAX counted rows. True when the
    queue is empty after it (the Store takes one batch per PC per 30 s, so no second one now)."""
    if not enabled():
        return False
    try:
        from frontend import __version__
    except Exception:
        __version__ = "0.0.0"
    rows, ids = _take(QUEUE_MAX_ROWS)
    if not rows:
        return True
    groups = _coalesce(rows, ids)
    batch = groups[:BATCH_MAX]
    body = {"installId": install_id(), "appVersion": __version__, "events": [g[0] for g in batch]}
    global _refused_in_a_row
    sent, _, reason = _send(body).partition(":")
    if sent == "refused" and reason in _TOO_LARGE and len(batch) > 1:
        _refused_in_a_row = 0
        _send_halves(body, batch, __version__)
        return False
    if sent == "refused":
        _refused_in_a_row += 1
        if _refused_in_a_row < REFUSED_DROP_AFTER:
            return False
    elif sent != "ok":
        return False
    _drop([i for g in batch for i in g[1]])
    if sent == "refused":
        _refused_in_a_row = 0
        _report_refused(reason, __version__)
        return False  # the report itself goes in the next batch
    _refused_in_a_row = 0
    return len(groups) <= BATCH_MAX


# ----------------------------------------------------------------------------- the thread

def _loop() -> None:
    last_flush = time.monotonic() - FLUSH_EVERY_S + FIRST_FLUSH_S
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
                _note_alive()
                _maybe_perf_sample()
            due = now - last_flush >= FLUSH_EVERY_S or queued() >= FLUSH_AT_ROWS
            if due and enabled():
                last_flush = now
                if not flush():
                    # Rows left (or the Store away): try again in a minute, not an hour.
                    last_flush = now - FLUSH_EVERY_S + FIRST_FLUSH_S
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


def _note_alive() -> None:
    """Save this run's last boot stage and uptime; the next start reads them if it never closed."""
    try:
        from frontend import boot_report

        # ponytail: reads boot_report's in-memory record; a public getter if this grows.
        state = boot_report._state
        stages = state.get("stages") or []
        stage = str(stages[-1][0]) if stages else ""
        started = float(state.get("started") or 0.0)
        # The last boot.json heartbeat's agent processes' memory (older beats have none).
        beat = state.get("heartbeat")
        agents_mb = beat.get("agents_mb") if isinstance(beat, dict) else None
    except Exception:
        stage, started, agents_mb = "", 0.0, None
    uptime = time.time() - started if started else (time.monotonic() - _opened_at if _opened_at else 0.0)
    seen: dict[str, Any] = {"stage": stage, "uptime_s": round(max(0.0, uptime))}
    seen.update(_memory_now())
    if isinstance(agents_mb, (int, float)) and not isinstance(agents_mb, bool):
        seen["agents_mb"] = round(agents_mb)
    _meta_set(META_LAST_SEEN, json.dumps(seen))


# The WebView2 tree scan is a whole process-table walk (~70 ms CPU with 70 children, Nov 7
# 2026): every 90 s heartbeat it made the usage thread the hot one on idle PCs. RSS stays live.
WEBVIEW_SCAN_S = 900.0
_webview_scan: tuple[float, int] | None = None  # (monotonic, bytes) of the last scan


def _memory_now() -> dict[str, float]:
    """This app's RSS and its WebView2 processes' private memory, in MB: an unclean exit
    says whether the run was out of memory (webview_browser_exit_oom, Oct 29 2026)."""
    global _webview_scan
    try:
        import psutil

        me = psutil.Process()
        out = {"memory_mb": round(me.memory_info().rss / 1048576)}
        now = time.monotonic()
        scan = _webview_scan
        if scan is None or now - scan[0] >= WEBVIEW_SCAN_S:
            web = 0
            for child in me.children(recursive=True):
                try:
                    if child.name().lower() == "msedgewebview2.exe":
                        web += getattr(child.memory_info(), "private", 0) or child.memory_info().rss
                except Exception:
                    continue
            scan = _webview_scan = (now, web)
        if scan[1]:
            out["webview_mb"] = round(scan[1] / 1048576)
        return out
    except Exception:
        return {}


def _last_seen() -> dict[str, Any]:
    """The previous run's stage (one of ev.BOOT_STAGES) and uptime_s, as far as it got."""
    try:
        seen = json.loads(_meta_get(META_LAST_SEEN) or "{}")
    except ValueError:
        return {}
    if not isinstance(seen, dict):
        return {}
    out: dict[str, Any] = {}
    if seen.get("stage"):
        out["stage"] = seen["stage"] if seen["stage"] in ev.BOOT_STAGES else "other"
    for k in ("uptime_s", "memory_mb", "webview_mb", "agents_mb"):
        if k in seen:
            out[k] = seen[k]
    return out


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
        seen = _last_seen() if unclean else {}
        _meta_set(META_RUNNING, "1")
        _note_alive()
        if not enabled():
            _mirror(False)
            return
        from frontend import __version__

        install_id()
        _mirror(True)
        last = _meta_get(META_LAST_VERSION) or ""
        if unclean:
            track("error.code", code="app_unclean_exit", where="ui", kind="crash", version=last, **seen)
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
                "fails": 0,
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
        why = {"kind": ev.kind_of(detail), "reason": ev.turn_reason(detail)} if outcome == "error" else {}
        track(
            "chat.turn",
            provider=meta["provider"],
            model_family=meta["model_family"],
            outcome=outcome,
            duration_s=time.monotonic() - float(meta["started"]),
            tool_calls_n=int(meta["tools"]),
            tool_fails_n=int(meta.get("fails") or 0),
            v=_app_version(),
            **why,
        )
    except Exception:
        pass


def turn_tool(run_id: str, *, failed: bool = False) -> None:
    """One more tool call in a Claude Code / Codex / Cursor turn (failed=True: one more failed
    call, already counted). Their calls are counted as tool.call by the shared tool server, a
    process with no turns, so every such chat.turn said "0 tool calls" (Oct 22 2026: 86% of
    turns, 20-minute ones included)."""
    try:
        with _lock:
            meta = _run_meta.get(run_id or "")
            if meta is not None:
                key = "fails" if failed else "tools"
                meta[key] = int(meta.get(key) or 0) + 1
    except Exception:
        pass


def fail_of(text: str) -> str:
    """The "fail" word of a tool's JSON answer ({"ok": false, "fail": "exit_nonzero", …}), else ""."""
    s = (text or "").lstrip()
    if not s.startswith("{") or '"fail"' not in s:
        return ""
    try:
        data = json.loads(s)
    except ValueError:
        return ""
    return str(data.get("fail") or "") if isinstance(data, dict) else ""


def tool_done(
    name: str, *, ok: bool, duration_ms: float, conv_id: str = "", args: dict[str, Any] | None = None, fail: str = "",
) -> None:
    """fail: why a failed call failed (ev.TOOL_FAILS); a failed call without one is "error"."""
    try:
        if not enabled():
            return
        fail = "" if ok else (fail or "error")
        with _lock:
            rid = _conv_run.get(conv_id or "")
            meta = _run_meta.get(rid or "")
            if meta is not None:
                meta["tools"] = int(meta["tools"]) + 1
                if fail and fail != "exit_nonzero":
                    meta["fails"] = int(meta.get("fails") or 0) + 1
            after_fail = _last_failed.get((conv_id, name), False) if conv_id else False
            if conv_id:
                _last_failed[(conv_id, name)] = not ok
                if len(_last_failed) > 2000:
                    for stale in list(_last_failed)[:1000]:
                        _last_failed.pop(stale, None)
        props: dict[str, Any] = {"tool": name, "ok": ok, "duration_s": float(duration_ms) / 1000.0}
        if fail:
            props["fail"] = fail
            # why: the three things a failed call can be, so exits and refusals never read as crashes.
            props["why"] = {"exit_nonzero": "exit_code", "refused": "refused"}.get(fail, "error")
        if after_fail:
            props["after_fail"] = "yes"
        if name in ev.COMMAND_TOOLS and isinstance(args, dict):
            props["cmd"] = ev.cmd_kind(args.get("command"))
        track("tool.call", **props)
        if name in ("skill_read_subskill", "uefn_skill", "skill_search") and isinstance(args, dict):
            pack = args.get("pack_id") or args.get("pack") or args.get("skill") or args.get("name")
            if pack:
                track("skill.used", skill=pack)
    except Exception:
        pass


def stuck(coding_agent: str, seconds: float, tool: str = "") -> None:
    # v: one heavy PC on an old build told apart from a spread (ans7k2v9d4m1x), as tool.byv does.
    track("agent.stuck", provider=ev.provider_of(coding_agent), after_s=seconds, tool=tool, v=_app_version())


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


_net_fail_sent: set[tuple[str, str]] = set()


def net_fail(code: str, stage: str, proxy: str) -> None:
    """The app couldn't reach the site (code: what it tried). Once per UTC day per PC per code
    and stage: the day is kept in ducky.db meta, so every process on the PC shares it (one
    1.2.483 PC sent 7 timeout rows in a day, one per process)."""
    if (code, stage) in _net_fail_sent:
        return
    _net_fail_sent.add((code, stage))
    key, today = f"usage.net_fail.{ev.code_of(code)}.{stage}", time.strftime("%Y-%m-%d", time.gmtime())
    if _meta_get(key) == today:
        return
    _meta_set(key, today)
    kind = "timeout" if stage == "timeout" else "offline" if stage in ("dns", "connect", "proxy") else "other"
    track("error.code", code=ev.code_of(code), where="store", kind=kind, version=_app_version(),
          net_stage=stage, proxy=proxy)


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


# What stopped a push or pull before it tried: the tool answer's own keys, else words in its error.
_REVISION_KEYS = (("conflict_markers", "conflict_markers"), ("locked_by_other", "locked"),
                  ("both_changed", "both_changed"), ("conflict_dialog", "conflict_dialog"), ("needs_save", "needs_save"))
_REVISION_WORDS = (("pull them first", "behind"), ("no island open", "no_island"),
                   ("could not read the repository", "repo_read"), ("could not list pending", "repo_read"))


def revision(op: str, out: dict[str, Any], took_s: float) -> None:
    """One source-control push or pull, from the tool's answer. The error text never leaves."""
    try:
        if out.get("ok"):
            track("revision.op", op=op, outcome="up_to_date" if out.get("up_to_date") else "ok", took_s=took_s)
            return
        error = str(out.get("error") or "")
        reason = next((r for k, r in _REVISION_KEYS if out.get(k)), "")
        reason = reason or next((r for w, r in _REVISION_WORDS if w in error.lower()), "")
        track("revision.op", op=op, outcome="blocked" if reason else "fail",
              reason=reason or ev.kind_of(error), took_s=took_s)
    except Exception:
        pass


def remote_connect(out: dict[str, Any]) -> None:
    """The PC's answer to one Remote View offer (remote_direct.rtc_connect)."""
    error = str(out.get("error") or "")
    outcome = ("ok" if out.get("answer") else "timeout" if "in time" in error
               else "old_protocol" if "protocol" in error else "bad_offer" if error.startswith("invalid") else "error")
    track("remote.connect", outcome=outcome)


def _remote_keys(name: str) -> set[str]:
    """The keys an ev.EVENTS remote.* spec reads: its props plus bucket source names."""
    spec = ev.EVENTS[name]
    return set(spec) | {rule[2] for rule in spec.values() if rule[0] == "bucket"}


def remote_link(report: dict[str, Any]) -> None:
    """One viewer report: link state/connect time/path, plus optional ``quality``, ``input``,
    ``load`` and ``end`` dicts (each one remote.<key> event). ``client``, ``net`` and
    ``viewer_version`` ride on all of them. Free text (``reason``) never leaves: only the
    spec's keys are passed on and ev.validate keeps listed values only."""
    common = {k: report[k] for k in ev.REMOTE_COMMON if k in report}
    for part in ("quality", "input", "load", "end"):
        sub = report.get(part)
        if isinstance(sub, dict):
            name = f"remote.{part}"
            keys = _remote_keys(name)
            track(name, **{**{k: v for k, v in sub.items() if k in keys}, **common})
    props: dict[str, Any] = {}
    if report.get("state"):
        state = str(report["state"])
        props["link"] = state if state in ("connecting", "live", "reconnecting", "failed") else "other"
    if report.get("candidate"):
        cand = str(report["candidate"])
        props["candidate"] = cand if cand in ("host", "srflx", "prflx", "relay") else "other"
    if "connect_ms" in report:
        props["connect_ms"] = report["connect_ms"]
    if props:
        track("remote.link", **props, **common)


PERF_SETTLE_S = 300.0


def _maybe_perf_sample() -> None:
    # The day's one sample came from the first heartbeat, while start-up threads still ran
    # (panel_startup_bg, ship_newest): 6 of 19 "idle" samples over 5% on Nov 2 2026. Wait for
    # steady state; a run shorter than this has no idle to measure.
    if _opened_at and time.monotonic() - _opened_at < PERF_SETTLE_S:
        return
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
        before = _thread_times(proc)
        props["idle_cpu_pct"] = proc.cpu_percent(interval=1.0)
        # Over 5%: which thread used it (46% of idle samples on Oct 31 2026, and one PC's
        # profile had no single hot thread, so the busy one has to be named across PCs).
        if props["idle_cpu_pct"] > 5.0:
            hot = _hot_thread(before, _thread_times(proc))
            if hot:
                props["hot"] = hot
    except Exception:
        pass
    if props:
        track("perf.sample", **props)


def _thread_times(proc: Any) -> dict[int, float]:
    """CPU seconds so far per native thread id; {} when psutil can't say."""
    try:
        return {t.id: t.user_time + t.system_time for t in proc.threads()}
    except Exception:
        return {}


def thread_slug(name: str) -> str:
    """A Python thread name as a short anonymous slug: what comes after ':' (a tool name in
    ``shared-mcp-call:<tool>``) and digits never leave. ``search-indexer-2`` -> ``search_indexer``."""
    s = re.sub(r"[^a-z_]+", "_", re.sub(r"\d+", "", name.split(":", 1)[0].lower()))
    return re.sub(r"_+", "_", s).strip("_")[:24].strip("_") or "native"


def _hot_thread(before: dict[int, float], after: dict[int, float]) -> str:
    """The thread that used the most CPU between two _thread_times; native when it runs no Python."""
    # Never the sampler itself: its own threads() reads (~110 ms each) fall between the two
    # snapshots, so on a quiet PC it named itself (3 of 5 idle samples over 5%, anf7q2w9e4r1t).
    sampler = getattr(_thread, "native_id", None)
    after = {t: v for t, v in after.items() if t != sampler}
    if not after:
        return ""
    tid = max(after, key=lambda t: after[t] - before.get(t, 0.0))
    if after[tid] - before.get(tid, 0.0) <= 0:
        return ""
    for t in threading.enumerate():
        if getattr(t, "native_id", None) == tid:
            return thread_slug(t.name)
    return "native"


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
    "workflow_done", "plugin_installed", "plugin_removed", "login", "popup", "revision", "remote_connect",
    "remote_link", "queued", "clear_queue",
    "debug_state", "reset_install_id", "json",
]
