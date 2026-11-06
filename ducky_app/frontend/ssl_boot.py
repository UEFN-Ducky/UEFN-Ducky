"""Python's SSL uses the Windows certificate store (truststore), set up once per process.

OpenSSL can build a different chain than Windows from the same store and report
"certificate has expired" on PCs where Edge connects fine. Importing truststore costs
~0.1 s; the GUI launch does it on a thread while it checks for a running Ducky (loopback
HTTP only), and waits for it before anything that can open an HTTPS connection.
"""
from __future__ import annotations

import threading

_thread: threading.Thread | None = None
_lock = threading.Lock()


def inject_now() -> None:
    """Never raises: without truststore Python keeps its own certificate store."""
    try:
        import truststore

        truststore.inject_into_ssl()
    except Exception:
        pass


def inject_in_background() -> None:
    global _thread
    with _lock:
        if _thread is None:
            _thread = threading.Thread(target=inject_now, daemon=True, name="truststore-inject")
            _thread.start()


def wait(timeout_s: float = 10.0) -> None:
    """Block until a background inject has finished (no-op when none was started)."""
    thread = _thread
    if thread is not None:
        thread.join(timeout_s)
