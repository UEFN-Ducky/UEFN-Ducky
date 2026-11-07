"""``--backend``: no webview or Tk, hello with the panel port, argv parsing, calls."""

from __future__ import annotations

import inspect
import io
import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from frontend.native_host import headless

DUCKY_APP = Path(__file__).resolve().parents[2]

_BACKEND_PROBE = textwrap.dedent(
    """
    import io, json, sys, types

    fake_workers = types.ModuleType("backend.workspace.diff_workers")
    fake_workers.enable = lambda: None
    sys.modules["backend.workspace.diff_workers"] = fake_workers

    from frontend import app_versions
    from frontend.native_host import headless, lifecycle
    from frontend.ui_web import webview_app

    class Api:
        def __init__(self):
            from frontend import boot_report

            boot_report.stage("panel_api_init")

        def bind_window(self, window, *, on_hide, on_exit):
            self.bound = window

        def ping(self, n):
            return n + 1

    class QuickShutdown(lifecycle.Shutdown):
        def __init__(self, api):
            super().__init__(api, exit_fn=lambda **_: None, wait_s=0)

    app_versions.mark_ready = lambda version: None
    from frontend import boot_report

    marks = []
    boot_report.mark = lambda state, **k: marks.append(state)
    real_stage = boot_report.stage
    boot_report.stage = lambda name: (marks.append("stage:" + name), real_stage(name))
    lifecycle.second_instance = lambda paths, links: (lifecycle.FIRST, None)
    lifecycle.watch_parent = lambda pid, on_dead: None
    lifecycle.Shutdown = QuickShutdown
    webview_app.make_background_work = lambda: (lambda: None)
    webview_app.boot_panel_api = Api
    webview_app.install_workspace_services = lambda: None
    webview_app.start_panel_server = lambda ui_port=None: ("http://127.0.0.1:4567/", False, "http://127.0.0.1:4567/")

    out = io.BytesIO()
    code = headless.run_backend(["UEFN-Ducky.exe", "--backend"], stdin=io.BytesIO(b""), stdout=out)
    lines = [json.loads(line) for line in out.getvalue().decode("utf-8").splitlines()]
    bad = sorted(m for m in ("webview", "tkinter", "_tkinter", "clr", "pythonnet") if m in sys.modules)
    sys.__stdout__.write(json.dumps({"code": code, "lines": lines, "bad": bad, "marks": marks}))
    """
)


@pytest.fixture(scope="module")
def backend_run(tmp_path_factory) -> dict:
    """One real ``run_backend`` in a fresh interpreter (the panel API and server stubbed)."""
    data = tmp_path_factory.mktemp("appdata")
    env = dict(os.environ)
    env.update(
        {
            "LOCALAPPDATA": str(data),
            "APPDATA": str(data),
            "UEFN_DUCKY_PANEL_PORT": "4568",
            "PYTHONPATH": str(DUCKY_APP),
            "DUCKY_SANDBOX": "1",
        }
    )
    proc = subprocess.run(
        [sys.executable, "-c", _BACKEND_PROBE],
        cwd=str(DUCKY_APP),
        env=env,
        capture_output=True,
        timeout=120,
    )
    assert proc.returncode == 0, proc.stderr.decode("utf-8", "replace")
    return json.loads(proc.stdout.decode("utf-8").strip().splitlines()[-1])


def test_backend_imports_no_webview_or_tk(backend_run) -> None:
    assert backend_run["bad"] == []
    assert backend_run["code"] == 0


def test_hello_carries_the_panel_port(backend_run) -> None:
    hello = next(line for line in backend_run["lines"] if line["t"] == "hello")
    assert hello["protocol"] == 1
    assert hello["port"] == 4567
    assert hello["origin"] == "http://127.0.0.1:4567"
    assert isinstance(hello["pid"], int) and hello["pid"] > 0
    assert hello["profile_dir"].endswith("webview")
    assert hello["app_version"]
    assert any(line.get("name") == "title" for line in backend_run["lines"][1:])


def test_backend_marks_the_launch_started_after_hello(backend_run) -> None:
    """boot.json says ok once the launcher has its hello, as the window start does when shown."""
    marks = backend_run["marks"]
    assert marks[marks.index("stage:hello") + 1] == "ok"


def test_stage_lines_reach_the_launcher_in_order(backend_run) -> None:
    """Each boot stage is one ``stage`` event; the ones before hello come before it."""
    lines = backend_run["lines"]
    kinds = [line.get("name") if line["t"] == "event" else line["t"] for line in lines]
    stages = [line["data"] for line in lines if line.get("name") == "stage"]
    assert [s["name"] for s in stages] == ["panel_api_init", "hello"]
    assert all(isinstance(s["ms"], int) and s["ms"] >= 0 for s in stages)
    assert kinds.index("stage") < kinds.index("hello") < len(kinds) - 1 - kinds[::-1].index("stage")


def test_run_signatures_unchanged() -> None:
    from frontend.ui_web import run as run_module
    from frontend.ui_web import webview_app

    assert list(inspect.signature(webview_app.run).parameters) == []
    assert list(inspect.signature(run_module.run).parameters) == []


def test_shared_server_start_is_a_module_function() -> None:
    from frontend.ui_web import webview_app

    for name in ("start_panel_server", "boot_panel_api", "make_background_work", "install_workspace_services"):
        assert callable(getattr(webview_app, name))
    assert "start_panel_server()" in inspect.getsource(webview_app._run_panel)


def test_run_diverts_to_backend(monkeypatch) -> None:
    from frontend.ui_web import run as run_module
    from frontend.ui_web import webview_app

    monkeypatch.setattr(sys, "argv", ["UEFN-Ducky.exe", "--backend"])
    monkeypatch.setattr(headless, "run_backend", lambda: 7)
    monkeypatch.setattr(webview_app, "run", lambda: pytest.fail("the window must not start"))
    with pytest.raises(SystemExit) as stop:
        run_module.run()
    assert stop.value.code == 7


def test_flags_come_out_and_files_and_links_stay(tmp_path) -> None:
    from frontend.open_files import cli_deep_links, cli_open_paths

    verse = tmp_path / "a.verse"
    verse.write_text("x", encoding="utf-8")
    argv = ["UEFN-Ducky.exe", "--backend", "--after-update", "123", str(verse), "uefn-ducky://open/chat/1"]
    assert headless.wants_backend(argv)
    assert headless.take_backend_flag(argv)
    assert headless.take_after_update(argv) == 123
    assert argv == ["UEFN-Ducky.exe", str(verse), "uefn-ducky://open/chat/1"]
    assert cli_open_paths(argv) == [str(verse.resolve())] or cli_open_paths(argv) == [str(verse)]
    assert cli_deep_links(argv) == ["uefn-ducky://open/chat/1"]
    assert not headless.take_backend_flag(argv)
    assert headless.take_after_update(argv) is None


def test_background_flag_sets_hidden_start(monkeypatch) -> None:
    from frontend.autostart import START_HIDDEN_ENV

    monkeypatch.setenv(START_HIDDEN_ENV, "0")
    monkeypatch.setenv("UEFN_DUCKY_PANEL_PORT", "4568")  # pick_panel_port leaves the PC alone
    monkeypatch.setattr(headless, "_std_binary", lambda which: None)  # stops right after parsing
    argv = ["UEFN-Ducky.exe", "--backend", "--background"]
    assert headless.run_backend(argv) == headless.EXIT_START_FAILED
    assert os.environ.get(START_HIDDEN_ENV) == "1"


def test_dispatch_call_public_methods_only() -> None:
    class Api:
        def add(self, a, b):
            return a + b

        def boom(self):
            raise RuntimeError("no")

        def _secret(self):
            return "x"

    api = Api()
    assert headless.dispatch_call(api, {"t": "call", "id": "1", "name": "add", "args": [2, 3]}) == {
        "t": "result",
        "id": "1",
        "ok": True,
        "value": 5,
    }
    failed = headless.dispatch_call(api, {"id": "2", "name": "boom"})
    assert failed["ok"] is False and "RuntimeError" in failed["error"]
    assert headless.dispatch_call(api, {"id": "3", "name": "_secret"})["ok"] is False
    assert headless.dispatch_call(api, {"id": "4", "name": "missing"})["ok"] is False
    assert headless.dispatch_call(api, {"id": "5", "name": "add", "args": "12"})["ok"] is False


def test_writer_lines_and_size_limit() -> None:
    out = io.BytesIO()
    writer = headless.LineWriter(out)
    assert writer.event("toast", {"title": "Hi ✓", "message": "m"})
    assert writer.send({"t": "result", "id": "big", "ok": True, "value": "x" * (headless.MAX_LINE_BYTES + 10)})
    assert not writer.send({"t": "event", "name": "toast", "data": {"m": "x" * (headless.MAX_LINE_BYTES + 10)}})
    lines = out.getvalue().split(b"\n")
    assert json.loads(lines[0]) == {"t": "event", "name": "toast", "data": {"title": "Hi ✓", "message": "m"}}
    big = json.loads(lines[1])
    assert big["id"] == "big" and big["ok"] is False
    assert lines[2] == b""


# --- under the launcher: the UI server on an internal port -------------------------------


def test_backend_ui_port_env(monkeypatch) -> None:
    monkeypatch.delenv(headless.BACKEND_UI_PORT_ENV, raising=False)
    assert headless.backend_ui_port() is None
    for raw, want in (("0", 0), (" 51234 ", 51234), ("x", None), ("70000", None), ("", None)):
        monkeypatch.setenv(headless.BACKEND_UI_PORT_ENV, raw)
        assert headless.backend_ui_port() == want
        assert headless.BACKEND_UI_PORT_ENV not in os.environ  # children never inherit it


@pytest.fixture
def internal_ui_server(tmp_path):
    """The real panel UI server on a free internal port; the public port is never bound."""
    from frontend.ui_web import panel_httpd

    (tmp_path / "index.html").write_text("<!doctype html><title>t</title>", encoding="utf-8")
    assert panel_httpd._server is None
    public = panel_httpd.PANEL_UI_HTTP_PORT
    url = panel_httpd.start_panel_ui_server(tmp_path, bind_port=0)
    try:
        yield panel_httpd, url, public, panel_httpd.panel_ui_bound_port()
    finally:
        panel_httpd._server.shutdown()
        panel_httpd._server.server_close()
        panel_httpd._server = None
        panel_httpd._root = None


def _post(port: int, origin: str | None, host: str | None = None) -> int:
    import http.client

    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    headers = {"Content-Length": "0", "Host": host or f"127.0.0.1:{port}"}
    if origin is not None:
        headers["Origin"] = origin
    try:
        conn.request("POST", "/__ducky_no_such_route", headers=headers)
        return conn.getresponse().status
    finally:
        conn.close()


def test_internal_port_keeps_the_public_origin(internal_ui_server) -> None:
    panel_httpd, url, public, internal = internal_ui_server
    assert internal not in (0, public)
    # What the page, panel.pid's hand-off and the IDE bridges use stays public.
    assert url == panel_httpd.panel_ui_http_url() == f"http://127.0.0.1:{public}/"
    assert panel_httpd.PANEL_UI_HTTP_PORT == public


def test_internal_port_accepts_the_launchers_origin_only(internal_ui_server) -> None:
    _panel_httpd, _url, public, internal = internal_ui_server
    launcher = f"http://127.0.0.1:{public}"
    assert _post(internal, launcher) != 403  # proxied with the internal Host
    assert _post(internal, launcher, host=f"127.0.0.1:{public}") != 403  # Host passed through
    assert _post(internal, f"http://127.0.0.1:{internal}") != 403  # the internal origin itself
    assert _post(internal, None) != 403
    assert _post(internal, "http://evil.example") == 403
    assert _post(internal, "http://127.0.0.1:1") == 403
    assert _post(internal, "null") == 403
