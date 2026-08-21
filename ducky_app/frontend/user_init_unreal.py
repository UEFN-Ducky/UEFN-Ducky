"""UEFN-Ducky boot module for Epic EditorToolset ForceEnablePython.

With UEFN MCP Toolsets enabled, UEFN only runs Engine plugin init_unreal scripts
(not Documents/UnrealEngine/Python and not island Content/Python). This module is
imported from EditorToolset's init_unreal.py (hook installed by UEFN-Ducky deploy).

Starts the Ducky listener on :4200 after a short deferred tick so Epic MCP can
bind :8000 first. Never restarts Epic MCP when it is already up.
"""

from __future__ import annotations

import os
import sys
import traceback
from pathlib import Path

_DEFER_TICKS = 45  # ~0.5–1s after ForceEnable — let Epic MCP Toolsets bind first
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
                "[MCP] UEFN-Ducky listener source missing in AppData. "
                "Start UEFN-Ducky.exe once, then restart UEFN. "
                f"Expected: {ed}"
            )
        except Exception:
            pass
        return
    os.environ.setdefault("UEFN_DUCKY_LISTENER_PORT", "4200")
    # Coexistence: never StartServer Epic MCP from this path — Toolsets own :8000.
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
    # Immediate path if slate ticks are unavailable.
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

        unreal.log_error("[MCP] UEFN-Ducky user init_unreal:\n" + traceback.format_exc())
    except Exception:
        pass
