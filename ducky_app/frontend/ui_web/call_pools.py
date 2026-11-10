"""Fixed worker pools for window bridge calls and panel HTTP requests.

pywebview starts a Thread per JS call and ThreadingHTTPServer one per request;
with 16 agents that reached hundreds of threads fighting over the GIL. Here every
call waits in a lane with a fixed number of workers instead:

- bridge lanes: ``urgent`` (chat load/send/stop), ``block`` (dialogs, voice,
  network: anything that can wait > 1 s) and ``bg`` (everything else);
- HTTP pools: ``normal``, ``streams`` (endless window streams), ``long``
  (20 s long-polls and RPC waits).

Identical list reads share one backend call (single-flight) and a 1 s cache that
``on_push`` clears when the matching event fires.
"""

from __future__ import annotations

import json
import logging
import queue
import sys
import threading
import time
import traceback
from typing import Any, Callable

_log = logging.getLogger(__name__)

# Bridge lanes stay at 14 threads together, whatever the burst.
POOL_SIZES: dict[str, int] = {"control": 2, "urgent": 2, "bg": 6, "block": 4}
CONTROL = frozenset({"cancel_agent", "ui_rpc_respond", "steer_message"})
HTTP_POOL_SIZES: dict[str, int] = {"normal": 16, "streams": 8, "long": 32}

URGENT = frozenset({
    "load_messages",
    "send_message",
    "steer_message",
    "cancel_agent",
    "run_held_messages",
    "ui_rpc_respond",
    "set_conversation_coding_agent",
    "plan_progress",
})

# Every PanelApi method that can wait more than a second: file dialogs, the snip
# overlay, voice helpers, network and LLM calls (the bridge-job allowlist, which
# the panel may also call directly). test_call_pools fails on a new pick_/wait_/
# voice_/…_poll method that is neither here nor reviewed as fast.
BLOCKING = frozenset({
    "pick_workflow_folder",
    "pick_workflow_files",
    "pick_project_path",
    "import_sound_file",
    "prompt_save_skill_pack_export",
    "import_skill_pack",
    "snip_screen",
    "wait_for_agent_idle",
    "voice_transcribe_audio",
    "voice_create_realtime_token",
    "voice_summarize_reply",
    "voice_win_tts_start",
    "voice_win_tts_voices_start",
    "voice_win_tts_poll",
    "voice_win_stt_prewarm",
    "voice_win_stt_start",
    "voice_win_stt_poll",
    "voice_win_stt_stop",
    "voice_win_stt_cancel",
    "voice_open_windows_settings",
    "plugin_llm_complete",
    "duckyos_login",
    "duckyos_store_download",
    "ducky_link_call",
    "ensure_starter_llm_gateways",
    "draft_skill_pack",
    "draft_subskill",
    "test_key",
    "test_uefn_plugin_secret",
    "test_mcp_plugin",
    "test_mcp_server",
    "get_context_usage",
    "compile_verse_project",
    "get_mcp_tools_catalog",
    "get_mcp_server_tools",
    "get_workflow_tools_catalog",
    "run_workflow",
    "run_workflow_node",
    "keep_workflow_preview",
    "test_workflow_node",
    "tester_list_devices",
    "tester_run_tests",
    "tester_results",
    "tester_simulate",
    "tester_create_chat",
    "apply_update",
})

# Shared by identical callers and kept for CACHE_TTL_S.
CACHED = frozenset({
    "terminal_list",
    "terminal_busy_many",
    "group_members",
    "list_running_agents",
    "list_all_conversations",
    "get_skill_info",
    "list_project_file_paths",
    "list_coding_agents",
    "list_agent_profiles",
    "list_mcp_servers",
    "list_workflows",
    "has_any_api_key",
})
# plugin_data is cached only for read ops (its second argument); any other op clears it.
_PLUGIN_DATA_READS = frozenset({"scope.get", "data.get", "data.list", "files.list"})
# Shared while one runs, never cached: these must always be fresh (D11).
SINGLE_FLIGHT_ONLY = frozenset({"get_settings", "get_project_info", "get_listener_status"})
CACHE_TTL_S = 1.0
# fbw0 group B (app 0.3 -> 1.4 GB over 12 h): every key (get_skill_info per skill, plugin_data per
# path, list reads per filter) stayed forever after its 1 s TTL, holding its encoded JSON result.
CACHE_MAX = 64
# Total encoded size kept; one result over a quarter of it is never cached.
CACHE_MAX_BYTES = 8 * 1024 * 1024

_CHATS = frozenset({"list_all_conversations", "group_members", "list_running_agents"})
_PLUGINS = frozenset({"list_mcp_servers", "get_skill_info", "list_coding_agents", "list_agent_profiles", "plugin_data"})
_TERMINALS = frozenset({"terminal_list", "terminal_busy_many"})

LANE_WAIT_TRACE_S = 2.0

_clock = time.monotonic


class _Pool:
    """Daemon worker threads started on demand up to ``size`` (ThreadPoolExecutor
    workers are joined at exit, so one 20 s long-poll would hold up quitting)."""

    def __init__(self, name: str, size: int) -> None:
        self.name = name
        self.size = max(1, int(size))
        self._q: queue.Queue[tuple[Callable[..., Any], tuple[Any, ...]]] = queue.Queue()
        self._lock = threading.Lock()
        self._threads = 0
        self._idle = 0

    def submit(self, fn: Callable[..., Any], *args: Any) -> None:
        with self._lock:
            # Preserve reliable delivery under load, including remote calls.
            self._q.put_nowait((fn, args))
            if self._q.qsize() > self._idle and self._threads < self.size:
                t = threading.Thread(target=self._work, name=f"{self.name}_{self._threads}", daemon=True)
                self._threads += 1
                t.start()

    def thread_count(self) -> int:
        return self._threads

    def _work(self) -> None:
        while True:
            with self._lock:
                self._idle += 1
            fn, args = self._q.get()
            with self._lock:
                self._idle -= 1
            try:
                fn(*args)
            except Exception:
                _log.exception("%s task failed", self.name)
            finally:
                del fn, args
                self._q.task_done()


_pools: dict[str, _Pool] = {}
_pools_lock = threading.Lock()


def _pool(key: str, name: str, size: int) -> _Pool:
    with _pools_lock:
        p = _pools.get(key)
        if p is None:
            p = _pools[key] = _Pool(name, size)
        return p


def lane_for(method: str) -> str:
    from frontend.ui_web.remote_link.input_lane import is_input_method

    # Remote pointer/key frames: their own two workers (remote_link.input_lane), never bg.
    if is_input_method(method):
        return "input"
    if method in CONTROL:
        return "control"
    if method in URGENT:
        return "urgent"
    if method in BLOCKING:
        return "block"
    return "bg"


def lane(name: str) -> _Pool:
    return _pool(f"ui:{name}", f"ui-call-{name}", POOL_SIZES[name])


def http_pool(kind: str) -> _Pool:
    return _pool(f"http:{kind}", f"http-{kind}", HTTP_POOL_SIZES[kind])


def submit(method: str, fn: Callable[..., Any], *args: Any) -> None:
    """Run ``fn(*args)`` on ``method``'s lane; a wait over 2 s goes to the perf log."""
    name = lane_for(method)
    queued = _clock()

    def run() -> None:
        waited = _clock() - queued
        if waited > LANE_WAIT_TRACE_S:
            _trace("ui_lane_wait", method, waited * 1000.0, lane=name, method=method, wait_ms=round(waited * 1000.0))
        fn(*args)

    if name == "input":
        from frontend.ui_web.remote_link.input_lane import input_lane

        input_lane().submit(run)
        return
    lane(name).submit(run)


def _trace(kind: str, name: str, ms: float, **meta: Any) -> None:
    try:
        from frontend.perf_trace import trace

        trace(kind, name, ms, **meta)
    except Exception:
        pass


# --- single-flight + 1 s cache ------------------------------------------------


class _Flight:
    __slots__ = ("done", "value", "error", "gen")

    def __init__(self, gen: int) -> None:
        self.done = threading.Event()
        self.value: str | None = None
        self.error: BaseException | None = None
        self.gen = gen


_share_lock = threading.Lock()
_flights: dict[tuple[str, str], _Flight] = {}
_cache: dict[tuple[str, str], tuple[float, str]] = {}
_cache_timer: threading.Timer | None = None
_gen = 0  # bumped by on_push: a call that started before a clear never fills the cache


def _prune_cache() -> None:
    """Under _share_lock; insertion order is also access order."""
    now = _clock()
    for key in [k for k, (until, _) in _cache.items() if until <= now]:
        del _cache[key]
    sizes = [(k, sys.getsizeof(k[0]) + sys.getsizeof(k[1]) + sys.getsizeof(v[1]) + 128)
             for k, v in _cache.items()]
    total = sum(size for _, size in sizes)
    for key, size in sizes:
        if len(_cache) <= CACHE_MAX and total <= CACHE_MAX_BYTES:
            break
        del _cache[key]
        total -= size


def _expire_cache() -> None:
    global _cache_timer
    with _share_lock:
        _cache_timer = None
        _prune_cache()
        _schedule_expiry()


def _schedule_expiry() -> None:
    global _cache_timer
    if _cache and _cache_timer is None:
        delay = max(0.01, min(until for until, _ in _cache.values()) - _clock())
        _cache_timer = threading.Timer(delay, _expire_cache)
        _cache_timer.daemon = True
        _cache_timer.start()


def encode(result: Any) -> str:
    """The escaped JSON pywebview puts inside ``{value: '...'}``."""
    return json.dumps(result).replace("\\", "\\\\").replace("'", "\\'")


def _cacheable(method: str, args: Any) -> bool:
    if method in CACHED:
        return True
    if method == "plugin_data" and isinstance(args, (list, tuple)) and len(args) >= 2:
        return str(args[1]) in _PLUGIN_DATA_READS
    return False


def call_shared(method: str, func: Callable[..., Any], args: Any) -> str:
    """Run ``func(*args)`` and return its encoded result. List reads and the
    settings/project/listener getters share one run between identical callers."""
    params = list(args) if isinstance(args, (list, tuple)) else ([] if args is None else [args])
    cache = _cacheable(method, params)
    if not cache and method not in SINGLE_FLIGHT_ONLY:
        if method == "plugin_data":
            # A write: the next read sees it, not a cached copy from before.
            try:
                return encode(func(*params))
            finally:
                _drop_cached(frozenset({"plugin_data"}))
        return encode(func(*params))
    try:
        key = (method, json.dumps(params, sort_keys=True))
    except (TypeError, ValueError):
        return encode(func(*params))
    with _share_lock:
        if cache:
            _prune_cache()
            hit = _cache.get(key)
            if hit is not None and hit[0] > _clock():
                _cache.pop(key)
                _cache[key] = hit
                return hit[1]
        flight = _flights.get(key)
        owner = flight is None
        if owner:
            flight = _flights[key] = _Flight(_gen)
    if not owner:
        flight.done.wait()
        if flight.error is not None:
            raise flight.error
        return flight.value or "null"
    try:
        flight.value = encode(func(*params))
    except BaseException as exc:
        flight.error = exc
        raise
    finally:
        with _share_lock:
            _flights.pop(key, None)
            if cache and flight.error is None and flight.gen == _gen:
                _store(key, flight.value or "null")
        flight.done.set()
    return flight.value


def _store(key: tuple[str, str], value: str) -> None:
    """Cache ``value`` (under ``_share_lock``); past CACHE_MAX or CACHE_MAX_BYTES, expired rows
    go first, then the oldest. An idle timer empties expired rows when no call comes."""
    _cache.pop(key, None)  # re-inserted last: insertion order is age order
    if sys.getsizeof(value) + sys.getsizeof(key[1]) > CACHE_MAX_BYTES // 4:
        return
    _cache[key] = (_clock() + CACHE_TTL_S, value)
    _prune_cache()
    _schedule_expiry()


def on_push(event_type: str) -> None:
    """Drop cached lists a pushed event makes stale."""
    kind = str(event_type or "")
    if kind.endswith("_settings_changed") or kind in ("settings_changed", "project_changed", "project_switched"):
        methods: frozenset[str] | None = None
    elif kind == "chats_changed" or kind.startswith("group"):
        methods = _CHATS
    elif kind == "uefn_plugins_changed":
        methods = _PLUGINS
    elif kind.startswith("terminal"):
        methods = _TERMINALS
    else:
        return
    _drop_cached(methods)


def _drop_cached(methods: frozenset[str] | None) -> None:
    global _gen
    with _share_lock:
        _gen += 1
        if methods is None:
            _cache.clear()
        else:
            for key in [k for k in _cache if k[0] in methods]:
                del _cache[key]


# --- bridge result script -------------------------------------------------------


def result_script(func_name: str, value_id: str, retval: str) -> str:
    return f'window.pywebview._returnValuesCallbacks["{func_name}"]["{value_id}"]({retval})'


def bridge_script(func_name: str, func: Callable[..., Any], param: Any, value_id: str) -> str:
    """What pywebview's ``js_bridge_call._call`` evaluates, built on the worker."""
    try:
        retval = f"{{value: '{call_shared(func_name, func, param)}'}}"
    except Exception as e:
        _log.error(traceback.format_exc())
        error = {"message": str(e), "name": type(e).__name__, "stack": traceback.format_exc()}
        retval = f"{{isError: true, value: '{encode(error)}'}}"
    return result_script(func_name, value_id, retval)
