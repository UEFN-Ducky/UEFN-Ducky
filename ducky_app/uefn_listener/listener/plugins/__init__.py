"""Import Store-plugin listener handlers from ``listener/plugins/<plugin_id>/``.

Each enabled desktop plugin may ship a ``listener/`` folder that deploy overlays
into AppData under this package. A broken plugin must not take down the listener.
"""

from __future__ import annotations

import importlib
import pkgutil
from pathlib import Path

_ROOT = Path(__file__).resolve().parent


def _load_plugin_packages() -> None:
    if not _ROOT.is_dir():
        return
    for info in pkgutil.iter_modules([str(_ROOT)]):
        name = info.name
        if not name or name.startswith("_"):
            continue
        try:
            importlib.import_module(f"listener.plugins.{name}")
        except Exception as exc:  # noqa: BLE001 — isolate bad plugin handlers
            try:
                import unreal

                unreal.log_warning(f"[ducky] plugin listener load failed ({name}): {exc}")
            except Exception:
                pass


_load_plugin_packages()
