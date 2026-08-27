# UEFN-Ducky managed init — do not edit
"""One boot file. Ducky writes this same text to:

- island ``Content/Python/init_unreal.py``
- ``Documents/UnrealEngine/Python/init_unreal.py``
- Engine EditorToolset ``ducky_listener_boot.py`` (imported from its init_unreal)

Loads the listener from ``%LOCALAPPDATA%/UEFN-Ducky/listener``. Defers ~45 ticks
so Epic MCP Toolsets can bind :8000 first. Port 4200. Disable: ``UEFN_DUCKY_DISABLE=1``.
"""

from __future__ import annotations

import os
import sys
import traceback
from pathlib import Path

_DEFER_TICKS = 45
_handle = None
_ticks = 0


def _start_ducky() -> None:
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
    os.environ.setdefault("UEFN_DUCKY_ENSURE_EPIC", "0")
    if str(ed) not in sys.path:
        sys.path.insert(0, str(ed))
    from listener.bootstrap import run

    run(ensure_epic=False)


def _tick(_dt: float) -> None:
    global _ticks, _handle
    _ticks += 1
    if _ticks < _DEFER_TICKS:
        return
    try:
        import unreal

        if _handle is not None:
            unreal.unregister_slate_post_tick_callback(_handle)
            _handle = None
    except Exception:
        _handle = None
    try:
        _start_ducky()
    except Exception:
        try:
            import unreal

            unreal.log_error("[MCP] UEFN-Ducky deferred start:\n" + traceback.format_exc())
        except Exception:
            pass


def _run() -> None:
    global _handle
    try:
        import unreal

        if hasattr(unreal, "register_slate_post_tick_callback"):
            _handle = unreal.register_slate_post_tick_callback(_tick)
            try:
                unreal.log("[MCP] UEFN-Ducky: deferred listener start (coexist with Epic MCP Toolsets)")
            except Exception:
                pass
            return
    except Exception:
        pass
    _start_ducky()


try:
    _run()
except Exception:
    try:
        import unreal

        unreal.log_error("[MCP] init_unreal:\n" + traceback.format_exc())
    except Exception:
        pass
