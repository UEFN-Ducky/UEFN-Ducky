"""panel_port() is read when called, so URLs follow a moved port pair."""

from frontend import settings
from frontend.ui_web import generated_images, tool_captures
from frontend.ui_web.panel_bind import panel_port


def test_panel_port_follows_a_move(monkeypatch):
    monkeypatch.setattr(settings, "PANEL_LISTENER_PORT", 4211)
    assert panel_port() == 4210
    monkeypatch.setattr(settings, "PANEL_LISTENER_PORT", 4213)
    assert panel_port() == 4212


def test_generated_image_url_uses_the_moved_port(monkeypatch):
    monkeypatch.setattr(settings, "PANEL_LISTENER_PORT", 4211)
    assert generated_images.build_generated_image_url("a.png").startswith("http://127.0.0.1:4210/")
    monkeypatch.setattr(settings, "PANEL_LISTENER_PORT", 4213)
    assert generated_images.build_generated_image_url("a.png") == "http://127.0.0.1:4212/generated-images/a.png"
    assert tool_captures.build_tool_capture_url("b.png").startswith("http://127.0.0.1:4212/")
