"""Bind the panel UI server so WinError 10013 (a Windows-refused port) never stops startup."""

from __future__ import annotations

from typing import Any, Callable


def bind_panel_server(make_server: Callable[[int], Any], bind_port: int | None, public_port: int) -> Any:
    """``--backend`` (``bind_port`` set): a refused internal port falls back to any free one; hello
    reports the bound port. The window (``bind_port`` None): settings.bind_with_fallback moves the
    pair. The caller reads the bound port from ``server.server_address``.

    ponytail: modules that copied PANEL_LISTENER_PORT - 1 at import keep the old port until
    restart (panel_port.json then has the new one for every process)."""
    if bind_port is None:
        from frontend.settings import bind_with_fallback

        return bind_with_fallback(make_server, public_port)
    try:
        return make_server(int(bind_port))
    except OSError:
        if not int(bind_port):
            raise
        return make_server(0)
