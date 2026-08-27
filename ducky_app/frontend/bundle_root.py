"""Locate bundled data (``bundle/uefn_listener``, templates) for PyInstaller and Nuitka."""

from __future__ import annotations

import sys
from pathlib import Path


def is_packaged_runtime() -> bool:
    """True when running from a frozen/packaged binary (PyInstaller or Nuitka), not a dev ``python`` tree."""
    if getattr(sys, "frozen", False):
        return True
    try:
        import __main__

        return bool(getattr(__main__, "__compiled__", None))
    except Exception:
        return False


def packaged_data_root() -> Path | None:
    """
    Directory that contains ``bundle/uefn_listener`` and ``frontend/init_unreal.py``
    in release layouts (PyInstaller ``_MEIPASS`` or Nuitka onefile extraction dir).
    """
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        base = Path(meipass)
        if (base / "bundle" / "uefn_listener").is_dir():
            return base
    try:
        import __main__

        mf = getattr(__main__, "__file__", None)
        if mf and getattr(__main__, "__compiled__", None):
            base = Path(mf).resolve().parent
            if (base / "bundle" / "uefn_listener").is_dir():
                return base
    except Exception:
        pass
    return None
