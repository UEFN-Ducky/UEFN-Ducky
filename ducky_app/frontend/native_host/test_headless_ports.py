"""Every bind done before hello, refused with WinError 10013 (English and French Windows text).

Before hello the app binds only through two places: the settings probe (``_port_reserved``,
used by ``pick_panel_port`` on both the --backend and the Python window path) and the panel UI
server (``start_panel_ui_server``: ``bind_port`` on --backend, ``PANEL_UI_HTTP_PORT`` in the
window). The event forwarder (agent_turns/push.py) only connects, and the listener port is
bound inside UEFN, not by this process. A bind test here fails while that bind is still fatal.
"""

from __future__ import annotations

import socket

import pytest

from frontend import settings

_TEXTS = {
    "en": "An attempt was made to access a socket in a way forbidden by its access permissions",
    "fr": "Une tentative d’accès à un socket de manière interdite par ses autorisations d’accès a été tentée",
}


def _refused(lang: str) -> OSError:
    exc = OSError(13, _TEXTS[lang])
    exc.winerror = 10013  # what Windows sets; built by hand so the test runs on any OS
    return exc


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.fixture
def refuse(monkeypatch):
    """refuse(lang, ports): every bind to one of ``ports`` raises 10013 in that language."""
    real_bind = socket.socket.bind

    def install(lang: str, ports: set[int]) -> None:
        def bind(sock, address):
            if isinstance(address, tuple) and address[1] in ports:
                raise _refused(lang)
            return real_bind(sock, address)

        monkeypatch.setattr(socket.socket, "bind", bind)

    return install


@pytest.fixture
def panel_root(tmp_path, monkeypatch):
    from frontend.ui_web import panel_httpd

    (tmp_path / "index.html").write_text("<!doctype html><title>t</title>", encoding="utf-8")
    monkeypatch.setattr(panel_httpd, "_server", None)
    monkeypatch.setattr(panel_httpd, "_root", None)
    monkeypatch.setattr(panel_httpd, "verify_panel_dist", lambda _root: None)
    monkeypatch.setattr(panel_httpd, "PANEL_UI_HTTP_PORT", panel_httpd.PANEL_UI_HTTP_PORT)
    # A moved pair is saved and pinned: keep that out of the real app data and environment.
    monkeypatch.setattr(settings, "default_app_data_dir", lambda: tmp_path / "app")
    monkeypatch.setattr(settings, "PANEL_LISTENER_PORT", settings.PANEL_LISTENER_PORT)
    monkeypatch.setenv(settings._PORT_PIN_ENV, "")
    monkeypatch.delenv(settings._PORT_PIN_ENV)
    yield panel_httpd, tmp_path
    if panel_httpd._server is not None:
        panel_httpd._server.shutdown()
        panel_httpd._server.server_close()


@pytest.mark.parametrize("lang", sorted(_TEXTS))
def test_bind_settings_probe_reads_10013_as_reserved(refuse, lang) -> None:
    """settings._port_reserved / pick_panel_port: both paths. Not fatal (catches it)."""
    port = _free_port()
    refuse(lang, {port})
    assert settings._port_reserved(port) is True


@pytest.mark.parametrize("lang", sorted(_TEXTS))
def test_bind_panel_ui_window_path_moves_off_10013(refuse, panel_root, monkeypatch, lang) -> None:
    """panel_httpd.py _PanelServer bind, Python window path (PANEL_UI_HTTP_PORT). Fatal today:
    the probe passed, then Windows refused the real bind (fbzi8z31qdpmk)."""
    panel_httpd, root = panel_root
    reserved = _free_port()
    monkeypatch.setattr(panel_httpd, "PANEL_UI_HTTP_PORT", reserved)
    refuse(lang, {reserved})
    url = panel_httpd.start_panel_ui_server(root)
    bound = panel_httpd._server.server_address[1]
    assert bound != reserved
    assert url == f"http://127.0.0.1:{bound}/"  # the window loads the port that is listening


@pytest.mark.parametrize("lang", sorted(_TEXTS))
def test_bind_panel_ui_backend_path_moves_off_10013(refuse, panel_root, lang) -> None:
    """panel_httpd.py _PanelServer bind, --backend path (bind_port from the launcher).
    Fatal today: exit 1 before hello."""
    panel_httpd, root = panel_root
    reserved = _free_port()
    refuse(lang, {reserved})
    panel_httpd.start_panel_ui_server(root, bind_port=reserved)
    assert panel_httpd.panel_ui_bound_port() not in (reserved, 0)
