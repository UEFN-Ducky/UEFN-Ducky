"""An agent's run_workflow runs in the app, not in the shared tool server that can restart
under it (Nov 3 2026: five releases died as "The process running this workflow ended.")."""

from __future__ import annotations

import asyncio
import json
import threading

from backend.automations import runner
from backend.tools.panel import panel_automations as pa


def _quiet(monkeypatch) -> list[tuple]:
    told: list[tuple] = []
    monkeypatch.setattr(pa, "_reveal_graph", lambda *a, **k: None)
    monkeypatch.setattr(pa, "release_plugin_of", lambda wid, payload: "")
    monkeypatch.setattr(pa, "_tell_caller", lambda chat, wid, out: told.append((chat, wid, out)))
    return told


def test_the_app_runs_it_when_it_answers(monkeypatch):
    _quiet(monkeypatch)
    asked: list[dict] = []
    monkeypatch.setattr("backend.panel.rpc.panel_rpc",
                        lambda method, params, timeout=30.0: asked.append({"method": method, **params}) or {"ok": True})
    monkeypatch.setattr(runner, "run_workflow", lambda *a, **k: (_ for _ in ()).throw(AssertionError("ran here")))
    out = json.loads(asyncio.run(pa.run_workflow("rel", prompt="ship it", caller_conv_id="lead",
                                                 payload={"_person_started": True, "x": 1})))
    assert out["ok"] and out["status"] == "running"
    (call,) = asked
    assert (call["method"], call["workflow_id"], call["run_id"], call["caller"]) == ("workflow_start", "rel", out["run_id"], "lead")
    assert call["prompt"] == "ship it" and call["payload"] == {"x": 1}  # an agent never claims a person's Run


def test_without_the_app_it_still_runs_here(monkeypatch):
    told = _quiet(monkeypatch)
    monkeypatch.setattr("backend.panel.rpc.panel_rpc", lambda method, params, timeout=30.0: {"error": "panel not open"})
    ran = threading.Event()
    monkeypatch.setattr(runner, "run_workflow", lambda wid, **k: ran.set() or {"ok": True, "id": wid, "run": k["run_id"]})
    out = json.loads(asyncio.run(pa.run_workflow("rel", caller_conv_id="lead")))
    assert out["ok"] and ran.wait(5)
    for _ in range(50):
        if told:
            break
        threading.Event().wait(0.05)
    assert told and told[0][0] == "lead"


def test_the_apps_rpc_starts_the_run_in_the_app(monkeypatch, tmp_path):
    from backend.panel import rpc
    from frontend.ui_web import panel_httpd

    monkeypatch.setattr(panel_httpd, "_server", None)
    monkeypatch.setattr(panel_httpd, "_root", None)
    monkeypatch.setattr(panel_httpd, "PANEL_UI_HTTP_PORT", 0)
    monkeypatch.setattr(panel_httpd, "verify_panel_dist", lambda _root: None)
    started: list[tuple] = []
    monkeypatch.setattr(pa, "start_background_run", lambda wid, run, caller, **k: started.append((wid, run, caller, k)))
    panel_httpd.start_panel_ui_server(tmp_path)
    server = panel_httpd._server
    monkeypatch.setattr(rpc, "_RPC_URL", f"http://127.0.0.1:{server.server_address[1]}/__panel_rpc")
    try:
        assert rpc.panel_rpc("workflow_start", {"workflow_id": "rel", "run_id": "r1", "caller": "lead",
                                                "prompt": "go", "payload": {"x": 1}}) == {"ok": True}
        assert started == [("rel", "r1", "lead", {"prompt": "go", "files": None, "payload": {"x": 1}, "trigger_id": ""})]
    finally:
        server.shutdown()
        server.server_close()


def test_a_background_run_drops_person_fields_and_tells_its_chat(monkeypatch):
    told = _quiet(monkeypatch)
    seen: dict = {}
    monkeypatch.setattr(runner, "run_workflow", lambda wid, **k: seen.update(k) or {"ok": True, "id": wid, "run": k["run_id"]})
    pa.start_background_run("rel", "r1", "lead", payload={"_person_started": True, "x": 1}).join(5)
    assert seen["payload"] == {"x": 1} and seen["caller_conv_id"] == "lead" and seen["run_id"] == "r1"
    assert told == [("lead", "rel", {"ok": True, "id": "rel", "run": "r1"})]
