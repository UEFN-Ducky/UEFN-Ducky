"""HTTP, phone, website and MCP group calls all use the process's one PanelApi."""
import json
import urllib.request

from frontend.ui_web import panel_api, panel_httpd


def _count_constructions(monkeypatch) -> list[object]:
    built: list[object] = []
    monkeypatch.setattr(panel_api.PanelApi, "__init__", lambda self: built.append(self))
    monkeypatch.setattr(panel_api, "_shared_api", None)
    return built


def test_http_requests_share_one_panel_api(monkeypatch, tmp_path):
    # Oct 10 2026: every /__panel_api request built a PanelApi. Each one re-read the model
    # catalog, started a catalog refresh and began with blank listener state, so an idle
    # status poll from the phone or Remote View swept the island every 8 s.
    built = _count_constructions(monkeypatch)
    monkeypatch.setattr(panel_httpd, "_server", None)
    monkeypatch.setattr(panel_httpd, "_root", None)
    monkeypatch.setattr(panel_httpd, "PANEL_UI_HTTP_PORT", 0)
    monkeypatch.setattr(panel_httpd, "verify_panel_dist", lambda _root: None)
    panel_httpd.start_panel_ui_server(tmp_path)
    server = panel_httpd._server
    url = f"http://127.0.0.1:{server.server_address[1]}/__panel_api/ui_rpc_pending_questions"
    try:
        for _ in range(10):
            req = urllib.request.Request(url, data=b"{}", headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=10) as resp:
                assert json.loads(resp.read())["ok"] is True
    finally:
        server.shutdown()
        server.server_close()
    assert len(built) == 1


def test_website_rpc_and_mcp_group_tools_share_one_panel_api(monkeypatch):
    from backend.tools.panel import ducky_panel
    from frontend.duckyos_account import dispatch_desktop_rpc

    built = _count_constructions(monkeypatch)
    monkeypatch.setattr(panel_api.PanelApi, "list_folders", lambda self: [], raising=False)
    for _ in range(5):
        assert dispatch_desktop_rpc("list_folders", {}) == {"ok": True, "result": []}
    apis = {id(ducky_panel._panel_api()) for _ in range(5)}
    assert len(built) == 1
    assert apis == {id(built[0])}
