"""Entry: ``py -m frontend.ui_web`` — React panel (dev or built dist).

``--backend`` serves the same panel with no window, for Ducky-Launcher.exe
(``frontend/native_host/headless.py``).
"""

from __future__ import annotations


def run() -> None:
    import sys

    # Ducky's own short calls (a chat's reads, a save, a panel call) each let go of the interpreter
    # lock and must get it back. At Python's 5 ms they waited a whole Windows timer tick behind any
    # busy thread (a plugin computing, a big reply being packed): 120 short database reads took
    # 3.6 s instead of 1 ms, and chats took 13 to 62 s to open while City Builder worked
    # (Nov 20 2026). Under 1 ms the wait rounds to none on Windows and the call gets its turn at
    # once; 0.001 changed nothing there (6 calls a second against 7). The price: three or more
    # threads computing at once use a core each and do a third less work.
    sys.setswitchinterval(0.0005)

    from frontend.native_host.headless import wants_backend

    if wants_backend(sys.argv):
        from frontend.native_host.headless import run_backend

        raise SystemExit(run_backend())
    from frontend.ui_web.webview_app import run as _run

    _run()


if __name__ == "__main__":
    run()
