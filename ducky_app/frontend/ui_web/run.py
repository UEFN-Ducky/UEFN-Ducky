"""Entry: ``py -m frontend.ui_web`` — React panel (dev or built dist).

``--backend`` serves the same panel with no window, for Ducky-Launcher.exe
(``frontend/native_host/headless.py``).
"""

from __future__ import annotations


def run() -> None:
    import sys

    from frontend.native_host.headless import wants_backend

    if wants_backend(sys.argv):
        from frontend.native_host.headless import run_backend

        raise SystemExit(run_backend())
    from frontend.ui_web.webview_app import run as _run

    _run()


if __name__ == "__main__":
    run()
