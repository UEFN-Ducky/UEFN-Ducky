"""Deployed verbatim as the project's ``init_unreal.py`` — the only file written into the project.

Loads the in-editor listener from ``%LOCALAPPDATA%/UEFN-Ducky/listener``, which UEFN-Ducky.exe
overwrites with the latest plaintext listener source on every launch. No HTTP, no cache, no
bundle — start UEFN-Ducky.exe once, then restart UEFN (or call ``reload_listener``).

Port: 4200 (fixed). Override with env ``UEFN_DUCKY_LISTENER_PORT``. Disable with ``UEFN_DUCKY_DISABLE=1``.
"""

from __future__ import annotations

import os
import sys
import traceback
from pathlib import Path


def _run() -> None:
    if os.environ.get("UEFN_DUCKY_DISABLE", "").strip().lower() in ("1", "true", "yes"):
        return
    appdata = os.environ.get("LOCALAPPDATA") or str(Path.home())
    ed = Path(appdata) / "UEFN-Ducky" / "listener"
    if not (ed / "listener").is_dir():
        try:
            import unreal

            unreal.log_warning(
                "[MCP] Listener source not found in AppData. Start UEFN-Ducky.exe once, then restart UEFN. "
                f"Expected: {ed}"
            )
        except Exception:
            pass
        return
    os.environ.setdefault("UEFN_DUCKY_LISTENER_PORT", "4200")
    if str(ed) not in sys.path:
        sys.path.insert(0, str(ed))
    from listener.bootstrap import run

    run()


try:
    _run()
except Exception:
    try:
        import unreal

        unreal.log_error("[MCP] init_unreal:\n" + traceback.format_exc())
    except Exception:
        pass
