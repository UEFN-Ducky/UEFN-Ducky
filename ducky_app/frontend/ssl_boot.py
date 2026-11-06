"""Python's SSL uses the Windows certificate store (truststore), set up once per process.

OpenSSL can build a different chain than Windows from the same store and report
"certificate has expired" on PCs where Edge connects fine (it can't fetch a root the PC
hasn't saved yet; Windows does). Importing truststore costs ~0.1 s; the GUI launch does it
on a thread while it checks for a running Ducky (loopback HTTP only), and waits for it
before anything that can open an HTTPS connection.

The process-wide patch is the fallback for third-party clients. Ducky's own HTTPS
(``https_context()``: the urllib openers and the raw-socket fetchers) asks for a Windows
context per connection, so it never depends on the patch having finished or worked.
"""
from __future__ import annotations

import logging
import ssl
import threading

_log = logging.getLogger(__name__)
_thread: threading.Thread | None = None
_lock = threading.Lock()
_warned = False


def _warn_once(what: str, exc: BaseException) -> None:
    global _warned
    if not _warned:
        _warned = True
        _log.warning("Windows certificate store unavailable (%s): %s: %s", what, type(exc).__name__, exc)


def inject_now() -> None:
    """Never raises: without truststore Python keeps its own certificate store (logged)."""
    try:
        import truststore

        truststore.inject_into_ssl()
    except Exception as exc:
        _warn_once("inject", exc)


def inject_in_background() -> None:
    global _thread
    with _lock:
        if _thread is None:
            _thread = threading.Thread(target=inject_now, daemon=True, name="truststore-inject")
            _thread.start()


def wait(timeout_s: float = 10.0) -> None:
    """Block until a background inject has finished (no-op when none was started).

    A slow PC (sign-in start, antivirus scanning the EXE) can take longer than the
    timeout; then the inject runs here instead, so nothing after this goes out on OpenSSL.
    """
    thread = _thread
    if thread is not None:
        thread.join(timeout_s)
        if thread.is_alive():
            inject_now()


def https_context() -> ssl.SSLContext:
    """A fresh client context verified by Windows (what Edge trusts).

    Fresh per connection: truststore holds a lock for the whole handshake, so one shared
    context would make parallel downloads wait on each other.
    """
    try:
        import truststore

        return truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    except Exception as exc:
        _warn_once("context", exc)
        return ssl.create_default_context()
