"""Single entry: GUI (default) or ``bridge`` for IDE-spawned MCP stdio.

Frozen builds are PyInstaller **one-file**: code and data live inside ``UEFN-Ducky.exe`` and are
unpacked to a temp directory (``sys._MEIPASS``) at startup. You do **not** ship or place
``.zip``, or other runtime assets next to the executable.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

# Bootloader leftovers. After Python starts, this process uses sys._MEIPASS;
# children that inherit these think they are still inside our extract dir.
_PYI_BOOT_ENV_KEYS = (
    "_PYI_APPLICATION_HOME_DIR",
    "_PYI_ARCHIVE_FILE",
    "_PYI_PARENT_PROCESS_LEVEL",
    "_PYI_SPLASH_IPC",
    "_MEIPASS2",
)


def scrub_pyinstaller_boot_env() -> None:
    for key in _PYI_BOOT_ENV_KEYS:
        os.environ.pop(key, None)


def _ensure_repo_on_path() -> str:
    """Return repo / MEIPASS root; ensure ``ducky_app`` is on sys.path for ``frontend`` / ``backend``."""
    sys.dont_write_bytecode = True
    if getattr(sys, "frozen", False):
        # One-file EXE: ``_MEIPASS`` is always set (extracted bundle root). Avoid relying on
        # ``dirname(executable)`` for imports — nothing is required beside the ``.exe``.
        root = getattr(sys, "_MEIPASS", None) or os.path.dirname(sys.executable)
        paths = [root]
    else:
        try:
            import __main__ as _main

            if getattr(_main, "__compiled__", None) and getattr(_main, "__file__", None):
                root = str(Path(_main.__file__).resolve().parent)
                paths = [root]
            else:
                frontend_dir = Path(__file__).resolve().parent
                ducky_app = frontend_dir.parent
                root = str(ducky_app.parent)
                paths = [root, str(ducky_app)]
        except Exception:
            frontend_dir = Path(__file__).resolve().parent
            ducky_app = frontend_dir.parent
            root = str(ducky_app.parent)
            paths = [root, str(ducky_app)]
    for p in paths:
        if p not in sys.path:
            sys.path.insert(0, p)
    return root


def _is_stdio_disconnect(exc: BaseException) -> bool:
    """True when Cursor/IDE closed the MCP stdio pipe (normal on reconnect)."""
    name = exc.__class__.__name__
    if name in ("BrokenResourceError", "BrokenPipeError", "EOFError", "ConnectionResetError"):
        return True
    if isinstance(exc, OSError):
        # Windows often reports Errno 22 (EINVAL) or 9 (EBADF) when the parent
        # closes the redirected stdout pipe — not a real bridge failure.
        if getattr(exc, "errno", None) in (9, 22, 32):  # EBADF, EINVAL, EPIPE
            return True
        msg = str(exc).lower()
        if "invalid argument" in msg or "broken pipe" in msg:
            return True
    if name == "ExceptionGroup":
        subs = getattr(exc, "exceptions", ())
        return bool(subs) and all(_is_stdio_disconnect(sub) for sub in subs)
    # Unwrap nested causes (anyio / TaskGroup wrappers)
    cause = getattr(exc, "__cause__", None) or getattr(exc, "__context__", None)
    if cause is not None and cause is not exc:
        return _is_stdio_disconnect(cause)
    return False


def _patch_mcp_request_responder_idempotent() -> None:
    """Cancel-after-handler must not kill the stdio TaskGroup (mcp SDK race).

    Upstream: python-sdk#2416 / #2949 — ``notifications/cancelled`` can set
    ``_completed`` before ``respond()``; the assert then crashes the bridge.
    No-op if the installed SDK already uses early-return guards.
    """
    try:
        import inspect

        from mcp.shared.session import RequestResponder
        from mcp.types import ErrorData
    except Exception:
        return

    if getattr(RequestResponder, "_ducky_idempotent_respond", False):
        return

    try:
        respond_src = inspect.getsource(RequestResponder.respond)
    except Exception:
        respond_src = ""
    if "assert not self._completed" not in respond_src and "if self._completed" in respond_src:
        return

    async def _respond(self, response):  # type: ignore[no-untyped-def]
        if not getattr(self, "_entered", False):
            raise RuntimeError("RequestResponder must be used as a context manager")
        # Already completed (e.g. concurrent cancellation) — skip silently.
        if getattr(self, "_completed", False):
            return
        if not getattr(self, "cancelled", False):
            self._completed = True
            await self._session._send_response(  # type: ignore[attr-defined]
                request_id=self.request_id, response=response
            )

    async def _cancel(self):  # type: ignore[no-untyped-def]
        if not getattr(self, "_entered", False):
            raise RuntimeError("RequestResponder must be used as a context manager")
        scope = getattr(self, "_cancel_scope", None)
        if scope is None:
            raise RuntimeError("No active cancel scope")
        scope.cancel()
        # Handler already responded — do not send a second JSON-RPC error.
        if getattr(self, "_completed", False):
            return
        self._completed = True
        await self._session._send_response(  # type: ignore[attr-defined]
            request_id=self.request_id,
            response=ErrorData(code=0, message="Request cancelled", data=None),
        )

    try:
        RequestResponder.respond = _respond  # type: ignore[method-assign]
        RequestResponder.cancel = _cancel  # type: ignore[method-assign]
        RequestResponder._ducky_idempotent_respond = True  # type: ignore[attr-defined]
    except Exception:
        pass


def run_bridge() -> None:
    """Stdio MCP server for IDE-spawned MCP (``py -m frontend bridge``).

    Critical path must reach ``mcp.run()`` in under a second. Loading 27 Store
    plugins + ship_newest + listener describe used to block ~20–30s before any
    tool answered — Cursor cancelled in-flight calls (``Interrupted: Cancelled``).
    Core ``backend.tools`` (workspace_*, etc.) register at import; plugins and
    dynamic listener tools join in a background thread.
    """
    _ensure_repo_on_path()
    # Plugin register() skips IDE mcp.json rewrites in this mode (reconnect loop).
    os.environ["UEFN_DUCKY_MCP_BRIDGE"] = "1"

    _patch_mcp_request_responder_idempotent()

    from backend import bridge
    from backend.server import mcp

    import backend.tools  # noqa: F401 — core MCP tools before plugins

    args = sys.argv[2:] if len(sys.argv) > 2 else []
    for i, arg in enumerate(args):
        if arg == "--port" and i + 1 < len(args):
            bridge.set_port_override(int(args[i + 1]))

    def _bridge_warmup() -> None:
        try:
            from backend.uefn_plugins.host import ensure_plugins_loaded

            ensure_plugins_loaded()
        except Exception:
            pass
        try:
            # Drop early tools/list cache so IDE clients see Discord/etc.
            from backend.bridge.plugin_gate import clear_mcp_tool_cache

            clear_mcp_tool_cache(mcp)
        except Exception:
            pass
        try:
            from frontend.ship_newest import ship_newest_everywhere

            # apply_ides=False: rewriting Cursor mcp.json from the bridge process
            # kills this stdio session mid-handshake.
            ship_newest_everywhere(
                apply_ides=False,
                force_skills=False,
                skip_if_recently_shipped=True,
            )
        except Exception:
            pass
        try:
            from frontend.appdata_maintenance import start_appdata_maintenance_async

            start_appdata_maintenance_async()
        except Exception:
            pass
        try:
            from backend.bridge.dynamic_tools import register_dynamic_listener_tools

            register_dynamic_listener_tools()
        except Exception:
            pass
        try:
            from backend.mcp_plugins.bridge_proxy import (
                schedule_nested_proxy_retries,
                sync_nested_mcp_proxies,
            )

            sync_nested_mcp_proxies()
            schedule_nested_proxy_retries()
        except Exception:
            pass

    import threading

    try:
        from backend.bridge.plugin_gate import install_bridge_plugin_gate

        # Keep mcp.run() instant; wait happens inside tools/list + tools/call.
        install_bridge_plugin_gate(mcp)
    except Exception:
        pass

    threading.Thread(target=_bridge_warmup, daemon=True, name="bridge-warmup").start()
    try:
        mcp.run()
    except KeyboardInterrupt:
        raise
    except BaseException as exc:
        if _is_stdio_disconnect(exc):
            return
        raise


def main() -> None:
    # Before ANY subprocess can be spawned —
    # panel and bridge modes both fork children.
    scrub_pyinstaller_boot_env()
    if len(sys.argv) > 1 and sys.argv[1] == "bridge":
        run_bridge()
    else:
        # Remote Deploy needs HTTP bundle on listener_port+1000. The IDE bridge also starts this,
        # but users who only open the panel EXE must still serve the zip so UEFN can fetch it.
        # Ship/skills/IDE sync runs async inside _run_panel so the window paints immediately.
        import time as _time

        # Wall clock from process entry — webview_app boot traces use this for total_ms.
        os.environ["UEFN_DUCKY_BOOT_T0"] = str(_time.perf_counter())
        _ensure_repo_on_path()
        try:
            from frontend.app_logging import configure as configure_logging

            configure_logging()
        except Exception:
            pass
        # Logo window before panel_api / webview imports — otherwise double-click is a blank wait.
        try:
            from frontend.early_splash import show as show_early_splash

            show_early_splash()
        except Exception:
            pass
        from frontend.ui_web.run import run

        try:
            run()
        except (KeyboardInterrupt, SystemExit):
            # SystemExit(0) is the normal second-launch handoff path
            # (webview_app raises it after forwarding Open-with / deep links).
            raise
        except BaseException as exc:
            try:
                from frontend.early_splash import dismiss as dismiss_early_splash

                dismiss_early_splash()
            except Exception:
                pass
            from frontend.ui_web.shutdown import fatal_error_and_exit

            fatal_error_and_exit(exc)


if __name__ == "__main__":
    main()
