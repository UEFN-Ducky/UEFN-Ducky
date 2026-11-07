"""host_calls.py: one fake call per kind, plus unknown names."""

from __future__ import annotations

import json
from pathlib import Path

from frontend.native_host import host_calls as hc
from frontend.native_host import inventory, protocol


class FakeApi:
    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def get_version(self):
        self.calls.append(("get_version",))
        return "1.2.471"

    def send_message(self, conv_id, text, opts=None):
        self.calls.append(("send_message", conv_id, text, opts))
        return {"ok": True, "conv": conv_id}

    def list_folders(self):
        raise RuntimeError("disk gone")

    def get_settings(self):
        return {"path": Path("C:/x"), "n": 1}

    def get_log(self):
        return "x" * (protocol.MAX_LINE_BYTES + 10)

    # present on the object but launcher-owned / not in the lean cut / not in INVENTORY
    def minimize_window(self):
        raise AssertionError("launcher-owned names must never run in python")

    def bind_window(self, *a):
        raise AssertionError("not in the lean cut")

    def browser_pane_open(self, pane_id):
        return {"ok": True, "pane": pane_id}

    def secret_helper(self):
        raise AssertionError("not a js_api method")


def _host():
    api = FakeApi()
    return hc.HostCalls(api), api


def test_python_call_runs_on_the_api_object():
    host, api = _host()
    assert host.dispatch("c1", "get_version", []) == protocol.result_ok("c1", "1.2.471")
    out = host.dispatch("c2", "send_message", ["conv-1", "hi", {"files": []}])
    assert out == protocol.result_ok("c2", {"ok": True, "conv": "conv-1"})
    assert api.calls[-1] == ("send_message", "conv-1", "hi", {"files": []})


def test_launcher_window_names_are_refused():
    host, _ = _host()
    for name in ("minimize_window", "toggle_maximize", "begin_native_window_move", "close_this_window",
                 "pick_project_path"):
        assert host.dispatch("c", name, []) == protocol.result_error("c", hc.ERR_HANDLED_BY_LAUNCHER), name


def test_not_lean_cut_names_are_refused():
    host, _ = _host()
    assert host.dispatch("c", "bind_window", ["p"]) == protocol.result_error("c", hc.ERR_NOT_IN_LEAN_CUT)


def test_browser_panes_run_in_python():
    host, _ = _host()
    assert host.dispatch("c", "browser_pane_open", ["p"]) == protocol.result_ok("c", {"ok": True, "pane": "p"})


def test_unknown_names():
    host, _ = _host()
    for name in ("secret_helper", "no_such_method", "_private", "__class__", "a.b", "", None):
        assert host.dispatch("c", name, []) == protocol.result_error("c", hc.ERR_UNKNOWN), name


def test_listed_name_missing_on_object_is_unknown():
    host, _ = _host()
    assert "get_models" in inventory.INVENTORY
    assert host.dispatch("c", "get_models", []) == protocol.result_error("c", hc.ERR_UNKNOWN)


def test_exception_becomes_error_result():
    host, _ = _host()
    assert host.dispatch("c", "list_folders", []) == protocol.result_error("c", "RuntimeError: disk gone")


def test_non_json_value_is_stringified():
    host, _ = _host()
    out = host.dispatch("c", "get_settings", [])
    assert out["ok"] and out["value"]["n"] == 1 and isinstance(out["value"]["path"], str)


def test_handle_line_round_trip_and_too_large():
    host, _ = _host()
    line = host.handle_line(protocol.encode(protocol.call("c9", "get_version")))
    assert json.loads(line) == {"t": "result", "id": "c9", "ok": True, "value": "1.2.471"}
    big = host.handle_line(protocol.encode(protocol.call("c10", "get_log")))
    assert protocol.decode(big) == protocol.result_error("c10", hc.ERR_TOO_LARGE)


def test_handle_line_ignores_non_calls_and_bad_lines():
    host, api = _host()
    assert host.handle_line(protocol.encode(protocol.bye("x"))) is None
    assert host.handle_line("not json") is None
    assert host.handle({"t": "event", "name": "quit", "data": {}}) is None
    assert api.calls == []


def test_fixture_calls_dispatch():
    host, _ = _host()
    lines = Path(inventory.__file__).with_name("fixtures").joinpath("valid.jsonl").read_text("utf-8").splitlines()
    calls = [protocol.decode(ln) for ln in lines if '"t":"call"' in ln]
    results = {c["name"]: host.handle(c) for c in calls}
    assert results["get_window_bounds"] == protocol.result_error("c1", hc.ERR_HANDLED_BY_LAUNCHER)
    assert results["send_message"]["ok"] is True


def test_api_built_lazily_once():
    made = []

    def factory():
        made.append(1)
        return FakeApi()

    host = hc.HostCalls(api_factory=factory)
    assert made == []
    host.dispatch("a", "unknown_x", [])
    assert made == []  # refused names never build the api
    host.dispatch("b", "get_version", [])
    host.dispatch("c", "get_version", [])
    assert made == [1]
