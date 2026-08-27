"""ping, status, shutdown, logs, execute_python."""

import io
import importlib
import os
import sys
import threading
import time
import traceback
from pathlib import Path
from typing import Any, Dict

import tkinter as tk
import unreal

from listener.bulk_removed import bulk_removed_error
from listener import config
from listener.dispatch import register
from listener.logutil import log_msg
from listener.main_thread import run_on_main_thread
from listener.serialize import serialize
from listener.state import accept_lock, command_queue, log_ring, metrics
from listener.tk_root import get_tk_root


@register("ping")
def cmd_ping() -> dict:
    from listener.capabilities import listener_capabilities
    from listener.dispatch import handler_names
    from listener.source_stamp import LOADED_STAMP

    return {
        "status": "ok",
        "version": config.PROTOCOL_VERSION,
        "python_version": sys.version,
        "port": unreal._mcp_bound_port,
        "timestamp": time.time(),
        # Fingerprint of the source the RUNNING code was imported from — the MCP
        # host compares this against the AppData tree and auto-reloads on mismatch.
        "source_stamp": LOADED_STAMP,
        "commands": handler_names(),
        "listener_capabilities": listener_capabilities(),
    }


@register("describe_commands")
def cmd_describe_commands() -> dict:
    """Name, params, and doc summary for every command.

    Lets MCP hosts auto-expose listener commands as first-class tools (Cursor /
    Claude see new tools on every connect, without rebuilding the host exe).
    """
    import inspect

    from listener.dispatch import _HANDLERS
    from listener.source_stamp import LOADED_STAMP

    out = []
    for name, fn in sorted(_HANDLERS.items()):
        try:
            params = []
            for p in inspect.signature(fn).parameters.values():
                item: dict = {"name": p.name}
                if p.default is inspect.Parameter.empty:
                    item["required"] = True
                elif isinstance(p.default, (str, int, float, bool)) or p.default is None:
                    item["default"] = p.default
                else:
                    item["default"] = str(p.default)
                params.append(item)
            doc = (inspect.getdoc(fn) or "").strip().splitlines()
            out.append({"name": name, "doc": doc[0] if doc else "", "params": params})
        except Exception:
            out.append({"name": name, "doc": "", "params": []})
    return {"commands": out, "count": len(out), "source_stamp": LOADED_STAMP}


@register("status")
def cmd_status() -> dict:
    uptime = time.time() - metrics["started_at"] if metrics["started_at"] > 0 else 0.0
    times = metrics["response_times_ms"]
    avg_ms = sum(times) / len(times) if times else 0.0
    from listener.capabilities import listener_capabilities
    from listener.dispatch import handler_names

    last_tick = float(getattr(unreal, "_mcp_last_tick_at", 0.0) or 0.0)
    tick_age = (time.time() - last_tick) if last_tick > 0 else None
    return {
        "running": unreal._mcp_server is not None,
        "version": config.PROTOCOL_VERSION,
        "port": unreal._mcp_bound_port,
        "uptime_sec": round(uptime, 1),
        "total_requests": metrics["total_requests"],
        "total_errors": metrics["total_errors"],
        "avg_response_ms": round(avg_ms, 2),
        "last_request_at": metrics["last_request_at"],
        "last_command": metrics["last_command"],
        "last_error": metrics["last_error"],
        "last_client_ping": metrics["last_client_ping"],
        "queue_size": command_queue.qsize(),
        "tick_age_sec": round(tick_age, 2) if tick_age is not None else None,
        "command_timings": list(metrics.get("command_timings") or [])[-40:],
        "commands": handler_names(),
        "listener_capabilities": listener_capabilities(),
    }


@register("shutdown")
def cmd_shutdown() -> dict:
    def _deferred_stop() -> None:
        time.sleep(0.5)
        from listener.runtime import stop_listener

        run_on_main_thread(stop_listener)

    threading.Thread(target=_deferred_stop, daemon=True).start()
    log_msg("Shutdown scheduled in 0.5s")
    return {"status": "shutting_down", "port": unreal._mcp_bound_port}


@register("get_log")
def cmd_get_log(last_n: int = 50) -> dict:
    return {"lines": log_ring[-last_n:]}


def _execute_python_blocked(code: str) -> str | None:
    low = code.lower()
    compact = low.replace(" ", "").replace("\n", "").replace("\t", "")
    if (
        "delete_asset(" in compact
        or "delete_directory(" in compact
        or "editorassetlibrary.delete_" in compact
    ):
        return (
            "STOP: execute_python blocked — never delete island content. "
            "Fix broken refs (search / reimport / relink / duplicate). "
            "Delete only in the Content Browser. "
            "Editor offline is not a delete queue — do not restart UEFN to delete."
        )
    if "for name in dir(unreal)" in compact:
        return (
            "STOP: execute_python blocked for Verse compile/hash/property discovery. "
            "Use get_verse_editables / list_verse_property_hashes / wire_verse_*. "
            "Do not ask the user to Build Verse."
        )
    if "valkyrie" in low and ("compile" in low or "build" in low):
        return (
            "STOP: execute_python blocked for Verse compile/hash/property discovery. "
            "Use get_verse_editables / list_verse_property_hashes / wire_verse_*. "
            "Do not ask the user to Build Verse."
        )
    if "scriptpropertyoverrides" in compact:
        return (
            "STOP: execute_python blocked for Verse compile/hash/property discovery. "
            "Use get_verse_editables / list_verse_property_hashes / wire_verse_*. "
            "Do not ask the user to Build Verse."
        )
    if "add_unique_transient_override" in low:
        return (
            "STOP: execute_python blocked for manual Verse save overrides. "
            "Use wire_verse_* or set_verse_editable — they mark Script + mangled property only."
        )
    if "mark_verse_wiring_overrides" in compact:
        return (
            "STOP: execute_python blocked for manual Verse wiring overrides. "
            "Use wire_verse_* or set_verse_editable."
        )
    if "_hash_cache" in low and ("=" in code or "seed" in low):
        return (
            "STOP: execute_python blocked for hash-cache seeding. "
            "Build Verse in UEFN, then list_verse_property_hashes(refresh=true)."
        )
    # Certain-death reflection: constructing an asset-owned UObject with no outer,
    # then stitching it into an asset, is a native EXCEPTION_ACCESS_VIOLATION that
    # Python cannot catch — it kills the whole editor. (new_object(cls, outer) with a
    # comma does NOT match these needles and stays allowed.)
    if "skeletalmeshsocket(" in compact:
        return (
            "STOP: execute_python blocked — unreal.SkeletalMeshSocket() direct construction "
            "crashes UEFN (asset-owned UObject with no outer). Use the "
            "list_skeleton_sockets / add_skeleton_socket / remove_skeleton_socket tools."
        )
    if "niagarasystemconversioncontext(" in compact:
        return (
            "STOP: execute_python blocked — unreal.NiagaraSystemConversionContext() has no "
            "backing system, so add_template_emitter/add_emitter dereference a null pointer and "
            "hard-crash UEFN (EXCEPTION_ACCESS_VIOLATION). Build Niagara with the "
            "create_niagara_system / get_niagara_system_info / set_niagara_component_parameter tools, "
            "or duplicate_asset an existing NS_ template and edit that."
        )
    if "subobjectdatasubsystem" in compact:
        return (
            "STOP: execute_python blocked — Blueprint SCS/subobject surgery via "
            "SubobjectDataSubsystem crashes UEFN builds. Reading BP components via "
            "load_asset + simple_construction_script is fine. For attachments, create a "
            "socket with add_skeleton_socket and attach to it, or ask the user to edit "
            "the Blueprint in the UEFN editor."
        )
    if "get_all_toolset_json_schemas" in compact or "get_toolset_json_schema" in compact:
        return (
            "STOP: execute_python blocked — ToolsetRegistry.get_all_toolset_json_schemas / "
            "get_toolset_json_schema dumps the UMGToolSet schema (~50KB+) through Python and "
            "hard-crashes UnrealEditorFortnite (EXCEPTION_ACCESS_VIOLATION). Use the umg_* "
            "tools (umg_capabilities, get_widget_blueprint_info, add_widget_to_tree, …) which "
            "call execute_tool with small known payloads only."
        )
    # Disk storms freeze the editor: execute_python runs on the Slate main thread.
    # A one-object get_editor_property("__verse_0x…") is allowed. Block only when
    # a mangled-name literal is paired with a real filesystem / install scan.
    walk_needles = (
        "os.walk(",
        "path.rglob(",
        "pathlib.path.rglob",
        "unrealeditorfortnite",
        "epic games\\fortnite",
        "epic games/fortnite",
        "program files\\epic games\\fortnite",
        "program files/epic games/fortnite",
        "deriveddatacache",
        ".uasset",
        ".umap",
    )
    walking = any(n in compact or n in low for n in walk_needles)
    mentions_hash = "__verse_0x" in compact or "__verse_0x" in low
    if walking and mentions_hash:
        return (
            "STOP: execute_python blocked — filesystem walks / Verse-hash binary scans freeze UEFN "
            "(runs on the editor main thread). Reading or writing a mangled "
            "`__verse_0x<HASH>_<Field>` property on one Script object is allowed. "
            "Use list_verse_property_hashes(refresh=true), get_verse_editables, or "
            "wire_verse_* instead of scanning disk."
        )
    if walking and any(
        n in compact or n in low
        for n in (
            "os.walk(",
            "path.rglob(",
            "pathlib.path.rglob",
            "unrealeditorfortnite",
            "epic games\\fortnite",
            "epic games/fortnite",
        )
    ):
        return (
            "STOP: execute_python blocked — filesystem walks freeze UEFN "
            "(runs on the editor main thread). Use list_verse_property_hashes(refresh=true), "
            "get_verse_editables, or workspace_* tools instead."
        )
    return None


@register("execute_python")
def cmd_execute_python(code: str) -> dict:
    msg = _execute_python_blocked(code)
    if msg:
        return {"result": None, "stdout": "", "stderr": msg}

    stdout_buf = io.StringIO()
    stderr_buf = io.StringIO()
    old_stdout, old_stderr = sys.stdout, sys.stderr

    exec_globals: Dict[str, Any] = {
        "__builtins__": __builtins__,
        "unreal": unreal,
        "tk": tk,
        "get_tk_root": get_tk_root,
        "result": None,
    }
    for attr, cls_name in [
        ("actor_sub", "EditorActorSubsystem"),
        ("asset_sub", "EditorAssetSubsystem"),
        ("level_sub", "LevelEditorSubsystem"),
    ]:
        try:
            cls = getattr(unreal, cls_name)
            exec_globals[attr] = unreal.get_editor_subsystem(cls)
        except Exception:
            pass

    try:
        sys.stdout, sys.stderr = stdout_buf, stderr_buf
        exec(code, exec_globals)
    except Exception:
        traceback.print_exc(file=stderr_buf)
    finally:
        sys.stdout, sys.stderr = old_stdout, old_stderr

    return {
        "result": serialize(exec_globals.get("result")),
        "stdout": stdout_buf.getvalue(),
        "stderr": stderr_buf.getvalue(),
    }


@register("reload_listener")
def cmd_reload_listener() -> dict:
    """Reload the listener from ``%LOCALAPPDATA%/UEFN-Ducky/listener`` and restart it (no UEFN restart).

    UEFN-Ducky.exe overwrites that folder with the latest source on launch, so this just purges the
    in-memory ``listener.*`` modules and re-imports them from disk. Runs the restart on a background
    thread — never from the Slate tick, where ``stop_listener`` would deadlock on the server join.
    """
    appdata = os.environ.get("LOCALAPPDATA") or str(Path.home())
    listener_dir = Path(appdata) / "UEFN-Ducky" / "listener"
    if not (listener_dir / "listener").is_dir():
        return {
            "ok": False,
            "error": "listener_missing",
            "message": f"Listener source not in AppData. Start UEFN-Ducky.exe, then restart UEFN. Expected: {listener_dir}",
        }
    port = int(getattr(unreal, "_mcp_bound_port", 0) or 4200)
    if bool(getattr(unreal, "_mcp_reload_in_progress", False)):
        return {
            "ok": True,
            "port": port,
            "reloaded": False,
            "restart": "already_scheduled",
            "message": "Listener reload already in progress.",
        }
    unreal._mcp_reload_in_progress = True

    def _deferred_restart() -> None:
        time.sleep(1.0)
        claimed_listener = False
        release_scheduled = False
        try:
            # The reload request itself must finish first. Then atomically reserve
            # the listener so an agent cannot start a tool while modules are being
            # replaced. Multiple panel/IDE bridges can request reload concurrently.
            deadline = time.time() + 30.0
            while time.time() < deadline:
                with accept_lock:
                    idle = (
                        not bool(getattr(unreal, "_mcp_in_flight", False))
                        and not bool(getattr(unreal, "_mcp_dispatching", False))
                        and command_queue.qsize() == 0
                    )
                    if idle:
                        unreal._mcp_in_flight = True
                        unreal._mcp_in_flight_since = time.time()
                        claimed_listener = True
                        break
                time.sleep(0.1)
            if not claimed_listener:
                log_msg("reload_listener: deferred restart cancelled; listener stayed busy")
                return

            if str(listener_dir) not in sys.path:
                sys.path.insert(0, str(listener_dir))

            # AppData deployment uses a directory swap. A second Ducky/IDE
            # process may briefly be completing that swap, so retry the import
            # instead of leaving the in-memory listener half-purged.
            import_error: Exception | None = None
            for _attempt in range(20):
                for name in [m for m in list(sys.modules) if m == "listener" or m.startswith("listener.")]:
                    del sys.modules[name]
                importlib.invalidate_caches()
                try:
                    import listener.handlers  # noqa: F401

                    import_error = None
                    break
                except (ImportError, OSError) as exc:
                    import_error = exc
                    time.sleep(0.1)
            if import_error is not None:
                raise import_error

            from listener.runtime import restart_listener

            restart_listener(port)

            # STALE-DISPATCH TRAP: the Slate post-tick callback registered at boot
            # still points at the OLD tick_handler (old dispatch table) — without this
            # swap, new commands 404 ("Unknown command") and old handler bugs keep
            # running even though ping lists the new commands. Swap ON THE GAME THREAD
            # (the old tick drains main_queue, so the swap executes inside it; UE
            # multicast delegates allow remove/add during broadcast).
            from listener.main_thread import run_on_main_thread
            from listener.tick import tick_handler as _fresh_tick

            def _swap_tick() -> None:
                old = getattr(unreal, "_mcp_tick_handle", None)
                if old is not None:
                    try:
                        unreal.unregister_slate_post_tick_callback(old)
                    except Exception:
                        pass
                unreal._mcp_tick_handle = unreal.register_slate_post_tick_callback(_fresh_tick)
                # Keep POSTs blocked until the fresh dispatch callback is installed.
                with accept_lock:
                    unreal._mcp_reload_in_progress = False
                    unreal._mcp_in_flight = False
                    unreal._mcp_in_flight_since = 0.0

            run_on_main_thread(_swap_tick)
            release_scheduled = True
            log_msg(f"reload_listener: restarted on port {unreal._mcp_bound_port}; tick swap scheduled")
        except Exception:
            log_msg("reload_listener restart failed:\n" + traceback.format_exc())
        finally:
            if not release_scheduled:
                with accept_lock:
                    unreal._mcp_reload_in_progress = False
                    if claimed_listener:
                        unreal._mcp_in_flight = False
                        unreal._mcp_in_flight_since = 0.0

    threading.Thread(target=_deferred_restart, daemon=True, name="mcp-listener-reload").start()
    return {
        "ok": True,
        "port": port,
        "reloaded": True,
        "restart": "scheduled",
        "message": "Reloading from AppData editor; wait ~3s before other tools.",
    }


@register("batch_commands")
def cmd_batch_commands(commands: list, stop_on_error: bool = True) -> dict:
    return bulk_removed_error()
