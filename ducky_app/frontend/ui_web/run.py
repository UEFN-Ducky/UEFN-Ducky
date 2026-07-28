"""Entry: ``py -m frontend.ui_web`` — React panel (dev or built dist)."""

from __future__ import annotations


def run() -> None:
    from frontend.ui_web.webview_app import run as _run

    _run()


if __name__ == "__main__":
    run()
