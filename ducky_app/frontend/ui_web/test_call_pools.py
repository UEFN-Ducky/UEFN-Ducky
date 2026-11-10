"""Bridge call lanes, single-flight and the 1 s cache (frontend.ui_web.call_pools)."""

from __future__ import annotations

import json
import re
import threading
import time
from pathlib import Path

import pytest

from frontend.ui_web import call_pools


@pytest.fixture(autouse=True)
def fresh(monkeypatch):
    monkeypatch.setattr(call_pools, "_pools", {})
    monkeypatch.setattr(call_pools, "_flights", {})
    monkeypatch.setattr(call_pools, "_cache", {})
    yield


def _bridge_threads() -> int:
    return sum(p.thread_count() for k, p in call_pools._pools.items() if k.startswith("ui:"))


def test_200_calls_stay_within_14_threads():
    assert sum(call_pools.POOL_SIZES.values()) <= 14
    done = threading.Semaphore(0)
    methods = ["list_workflows", "load_messages", "pick_project_path", "group_members"]
    for i in range(200):
        call_pools.submit(methods[i % 4], lambda: (time.sleep(0.002), done.release()))
    for _ in range(200):
        assert done.acquire(timeout=10)
    assert _bridge_threads() <= 14
    names = {t.name for t in threading.enumerate()}
    assert any(n.startswith("ui-call-bg_") for n in names)


def test_urgent_call_finishes_while_background_sleeps():
    release = threading.Event()
    for _ in range(100):
        call_pools.submit("list_workflows", release.wait, 10)
    got = threading.Event()
    t0 = time.monotonic()
    call_pools.submit("load_messages", got.set)
    assert got.wait(2)
    assert time.monotonic() - t0 < 1.0
    release.set()


def test_blocking_call_does_not_delay_background():
    release = threading.Event()
    for _ in range(call_pools.POOL_SIZES["block"] + 2):
        call_pools.submit("pick_project_path", release.wait, 10)
    got = threading.Event()
    call_pools.submit("list_workflows", got.set)
    assert got.wait(2)
    release.set()


def test_lane_for():
    assert call_pools.lane_for("send_message") == "urgent"
    assert call_pools.lane_for("voice_transcribe_audio") == "block"
    assert call_pools.lane_for("group_members") == "bg"
    assert call_pools.lane_for("window_input") == "input"
    assert call_pools.lane_for("cancel_agent") == "control"


def test_remote_input_starts_at_once_behind_300_slow_bg_calls(monkeypatch):
    """The phone's pointer/key frames reach window_input through the bridge: never behind bg."""
    from frontend.ui_web.remote_link import input_lane

    monkeypatch.setattr(input_lane, "_lane", None)
    release = threading.Event()
    for _ in range(300):
        call_pools.submit("list_project_file_paths", release.wait, 10)
    got = threading.Event()
    t0 = time.monotonic()
    call_pools.submit("window_input", got.set)
    try:
        assert got.wait(2)
        assert time.monotonic() - t0 < 0.1
        assert _bridge_threads() <= 14
    finally:
        release.set()


def test_stop_has_reserved_workers():
    release = threading.Event()
    for _ in range(call_pools.POOL_SIZES["urgent"]):
        call_pools.submit("send_message", release.wait, 5)
    stopped = threading.Event()
    call_pools.submit("cancel_agent", stopped.set)
    try:
        assert stopped.wait(.25)
    finally:
        release.set()


def test_chat_loads_never_wait_behind_busy_sends():
    """Scrolling up loaded nothing while a team kept both urgent workers busy (Nov 10 2026)."""
    assert call_pools.lane_for("load_messages") == "chat"
    release = threading.Event()
    for _ in range(call_pools.POOL_SIZES["urgent"] + 3):
        call_pools.submit("send_message", release.wait, 5)
        call_pools.submit("plan_progress", release.wait, 5)
    loaded = threading.Event()
    call_pools.submit("load_messages", loaded.set)
    try:
        assert loaded.wait(.25)
    finally:
        release.set()


def test_idle_worker_releases_last_task():
    import weakref
    class Payload:
        pass
    value = Payload()
    ref = weakref.ref(value)
    done = threading.Event()
    pool = call_pools._Pool("release", 1)
    pool.submit(lambda arg: done.set(), value)
    del value
    assert done.wait(2)
    pool._q.join()
    assert ref() is None


def test_cache_has_entry_byte_and_idle_expiry_limits(monkeypatch):
    monkeypatch.setattr(call_pools, "CACHE_MAX", 4)
    monkeypatch.setattr(call_pools, "CACHE_MAX_BYTES", 4096)
    for n in range(30):
        call_pools.call_shared("group_members", lambda k: "x" * 200, [n])
    assert len(call_pools._cache) <= 4
    call_pools.call_shared("group_members", lambda: "x" * 4096, [])
    assert ("group_members", "[]") not in call_pools._cache
    deadline = time.monotonic() + 3
    while call_pools._cache and time.monotonic() < deadline:
        time.sleep(.02)
    assert not call_pools._cache


# Looked at and fast: return at once (jobs run elsewhere) or a short poll.
_REVIEWED_FAST = {
    "bridge_job_start", "bridge_job_poll", "plugin_tts_poll", "plugin_tts_voices_poll",
    "plugin_llm_poll", "plugin_translate_batch_poll", "plugin_data", "plugin_data_copy_to_space",
    "plugin_data_open_web", "consume_pending_open_files", "terminal_read_output",
}
_LONG_NAME = re.compile(r"^    def ((?!_)\w*(?:pick_|dialog|wait_|_poll|long_poll|voice_|bridge_job_|terminal_read|open_file_dialog|select_folder)\w*)\(", re.M)


def test_every_maybe_long_panel_method_has_a_lane_decision():
    root = Path(call_pools.__file__).resolve().parent
    found: set[str] = set()
    for path in root.glob("panel_api*.py"):
        found.update(_LONG_NAME.findall(path.read_text(encoding="utf-8")))
    assert found, "PanelApi sources not found"
    unknown = sorted(found - call_pools.BLOCKING - _REVIEWED_FAST)
    assert not unknown, f"put these in call_pools.BLOCKING or review them as fast: {unknown}"


# --- result script ---------------------------------------------------------------


def _pywebview_script(func_name: str, value_id: str, result) -> str:
    # webview/util.py js_bridge_call._call, success branch
    result = json.dumps(result).replace('\\', '\\\\').replace("'", "\\'")
    retval = f"{{value: '{result}'}}"
    return f'window.pywebview._returnValuesCallbacks["{func_name}"]["{value_id}"]({retval})'


def test_result_script_matches_pywebview():
    value = {"a": "it's \\ \"quoted\"", "n": [1, 2.5, None], "u": "ü"}
    got = call_pools.bridge_script("get_thing", lambda x: {**value, "x": x}, ["arg"], "v1")
    assert got == _pywebview_script("get_thing", "v1", {**value, "x": "arg"})


def test_error_result_uses_is_error_shape():
    def boom():
        raise ValueError("nope 'x'")

    got = call_pools.bridge_script("do_it", boom, [], "v9")
    prefix = 'window.pywebview._returnValuesCallbacks["do_it"]["v9"]({isError: true, value: \''
    assert got.startswith(prefix) and got.endswith("'})")
    raw = got[len(prefix):-3].replace("\\'", "'").replace("\\\\", "\\")
    err = json.loads(raw)
    assert err["name"] == "ValueError" and err["message"] == "nope 'x'" and "Traceback" in err["stack"]


# --- single-flight + cache ----------------------------------------------------------


def test_65_identical_group_members_calls_run_once():
    calls = []
    gate = threading.Event()

    def group_members(gid):
        calls.append(gid)
        gate.wait(5)
        return [{"id": gid}]

    out: list[str] = []
    lock = threading.Lock()

    def caller():
        r = call_pools.call_shared("group_members", group_members, ["g1"])
        with lock:
            out.append(r)

    threads = [threading.Thread(target=caller) for _ in range(65)]
    for t in threads:
        t.start()
    time.sleep(0.2)
    gate.set()
    for t in threads:
        t.join(5)
    assert len(calls) == 1
    assert out == [json.dumps([{"id": "g1"}])] * 65
    # Cached for a second: no new backend call.
    call_pools.call_shared("group_members", group_members, ["g1"])
    assert len(calls) == 1
    # Different args are a different call.
    call_pools.call_shared("group_members", group_members, ["g2"])
    assert calls == ["g1", "g2"]


def test_each_caller_gets_its_own_value_id():
    a = call_pools.bridge_script("list_workflows", lambda: [1], [], "id-a")
    b = call_pools.bridge_script("list_workflows", lambda: [2], [], "id-b")
    assert '["id-a"]' in a and '["id-b"]' in b
    assert "'[1]'" in b  # second call served from the cache


def test_push_clears_matching_cache():
    n = [0]

    def lister():
        n[0] += 1
        return n[0]

    assert call_pools.call_shared("list_all_conversations", lister, []) == "1"
    assert call_pools.call_shared("list_all_conversations", lister, []) == "1"
    call_pools.on_push("terminal_output")  # unrelated: kept
    assert call_pools.call_shared("list_all_conversations", lister, []) == "1"
    call_pools.on_push("chats_changed")
    assert call_pools.call_shared("list_all_conversations", lister, []) == "2"
    call_pools.on_push("appearance_settings_changed")
    assert call_pools.call_shared("list_all_conversations", lister, []) == "3"


def test_cache_expires(monkeypatch):
    now = [0.0]
    monkeypatch.setattr(call_pools, "_clock", lambda: now[0])
    n = [0]

    def lister():
        n[0] += 1
        return n[0]

    call_pools.call_shared("list_workflows", lister, [])
    now[0] = 0.9
    call_pools.call_shared("list_workflows", lister, [])
    now[0] = 1.1
    call_pools.call_shared("list_workflows", lister, [])
    assert n[0] == 2


def test_get_settings_is_never_cached():
    n = [0]

    def get_settings():
        n[0] += 1
        return {"n": n[0]}

    call_pools.call_shared("get_settings", get_settings, [])
    time.sleep(0.1)
    call_pools.call_shared("get_settings", get_settings, [])
    assert n[0] == 2


def test_plugin_data_reads_cached_writes_clear():
    store = {"k": 1}

    def plugin_data(pid, op, params=None):
        if op == "data.put":
            store["k"] = params["value"]
            return {"ok": True}
        return {"ok": True, "value": store["k"]}

    get = ["p", "data.get", {"key": "k"}]
    assert json.loads(call_pools.call_shared("plugin_data", plugin_data, get))["value"] == 1
    call_pools.call_shared("plugin_data", plugin_data, ["p", "data.put", {"key": "k", "value": 2}])
    assert json.loads(call_pools.call_shared("plugin_data", plugin_data, get))["value"] == 2


def test_shared_error_reaches_every_caller():
    gate = threading.Event()

    def bad():
        gate.wait(5)
        raise RuntimeError("down")

    errors: list[str] = []

    def caller():
        try:
            call_pools.call_shared("list_mcp_servers", bad, [])
        except RuntimeError as exc:
            errors.append(str(exc))

    threads = [threading.Thread(target=caller) for _ in range(5)]
    for t in threads:
        t.start()
    time.sleep(0.1)
    gate.set()
    for t in threads:
        t.join(5)
    assert errors == ["down"] * 5
    assert call_pools._cache == {}


def test_lane_wait_over_2s_is_traced(monkeypatch):
    from frontend import perf_trace

    traced: list[tuple] = []
    monkeypatch.setattr(perf_trace, "trace", lambda kind, name="", ms=0.0, **meta: traced.append((kind, meta)))
    now = [0.0]
    monkeypatch.setattr(call_pools, "_clock", lambda: now[0])
    ran = threading.Event()
    gate = threading.Event()
    for _ in range(call_pools.POOL_SIZES["urgent"]):
        call_pools.submit("send_message", gate.wait, 5)
    call_pools.submit("set_conversation_coding_agent", ran.set)
    time.sleep(0.1)
    now[0] = 2.5
    gate.set()
    assert ran.wait(5)
    waits = [m for k, m in traced if k == "ui_lane_wait"]
    assert waits and waits[-1]["lane"] == "urgent" and waits[-1]["method"] == "set_conversation_coding_agent"
