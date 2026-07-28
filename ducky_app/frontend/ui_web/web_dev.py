"""Dev panel: Vite hot-reload + WebView2 DevTools (inspector)."""

from __future__ import annotations

import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

_DEFAULT_VITE = "http://127.0.0.1:5173"


def _truthy(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in ("1", "true", "yes", "on")


def is_frozen_dev_exe() -> bool:
    """True when the frozen binary name marks a dev build (``UEFN-Ducky-Dev-*.exe``)."""
    if not getattr(sys, "frozen", False):
        return False
    stem = Path(sys.executable).stem.lower()
    return stem.startswith("uefn-ducky-dev") or stem == "uefn-ducky-dev"


def is_dev_panel() -> bool:
    """Inspector + optional Vite URL (env, ``--dev``, or dev EXE name)."""
    if os.environ.get("UEFN_DUCKY_WEB_DEV", "").strip():
        return True
    if _truthy("UEFN_DUCKY_DEV"):
        return True
    if "--dev" in sys.argv:
        return True
    return is_frozen_dev_exe()


def _vite_url() -> str:
    return os.environ.get("UEFN_DUCKY_WEB_DEV", "").strip() or _DEFAULT_VITE


def _vite_reachable(url: str, *, timeout: float = 0.45) -> bool:
    probe = url.rstrip("/") + "/"
    try:
        with urllib.request.urlopen(probe, timeout=timeout) as resp:
            return 200 <= int(getattr(resp, "status", 200)) < 500
    except (OSError, urllib.error.URLError, ValueError):
        return False


def resolve_web_url(*, bundled_index_uri: str, bundled_http_url: str | None = None) -> tuple[str, bool]:
    """
    Return ``(url, debug)`` for pywebview.

    Dev mode prefers Vite when reachable; otherwise falls back to the embedded ``dist`` build
    but still enables DevTools. Production prefers ``bundled_http_url`` over ``file://``.
    """
    bundled = bundled_http_url or bundled_index_uri
    debug = is_dev_panel()
    if not debug:
        return bundled, False

    vite = _vite_url().rstrip("/") + "/"
    if _vite_reachable(vite):
        return vite, True

    # Vite not running — ship embedded UI but keep inspector open for debugging.
    return bundled, True
