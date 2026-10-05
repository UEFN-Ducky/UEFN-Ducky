"""Host-only project startup, usable before either editor bridge exists."""

from __future__ import annotations

import contextvars
import threading
from typing import Any, Callable

_open_lock = threading.Lock()


def until_stopped(wait: Callable[..., dict[str, Any]], *args: Any, **kwargs: Any) -> dict[str, Any]:
    """Run a long wait (UEFN ready, a player, a log line) but give up as soon as the
    workflow running it is stopped: the step ends (and lets go of the open lock) at
    once; the wait itself runs out in the background."""
    from backend.automations.runner import _CANCEL

    cancel = _CANCEL.get()
    if cancel is None:
        return wait(*args, **kwargs)
    box: dict[str, Any] = {}
    done = threading.Event()
    context = contextvars.copy_context()

    def work() -> None:
        try:
            box["value"] = context.run(wait, *args, **kwargs)
        except BaseException as exc:
            box["error"] = exc
        finally:
            done.set()

    threading.Thread(target=work, name="workflow-uefn-wait", daemon=True).start()
    while not done.wait(0.1):
        if cancel.is_set():
            return {"ok": False, "stopped": True, "error": "Stopped"}
    if "error" in box:
        raise box["error"]
    return box["value"]


def open_project(project: str | None, *, timeout: float = 180, wait: bool = True) -> dict[str, Any]:
    from frontend import window_view
    from frontend.ui_web.project_switch import switch_panel_project

    # Resolve before touching the selected project or launching a process.
    path = window_view.uefnproject_path(project)
    with _open_lock:
        if window_view._uefn_running():
            # Never launch a duplicate editor or silently close a different island.
            ready = until_stopped(window_view.wait_uefn_ready, root=str(path), timeout=timeout if wait else 1)
            if ready.get("stopped"):
                return ready
            if not ready.get("ok"):
                return {**ready, "error": "UEFN is already running but the requested project is not ready. "
                        "Use Restart UEFN to switch projects, or check the editor's login / loading screen."}
            switch_panel_project(path=str(path.parent), background_deploy=False)
            return {"ok": True, "path": str(path), "already_running": True, "ready": ready}

        # The normal project switch installs the managed listener bootstrap and
        # enables Python before the editor starts, also selecting the workspace
        # that downstream pipeline tools will use.
        switch_panel_project(path=str(path.parent), background_deploy=False)
        launched = window_view.launch_uefn_project(str(path))
        if not launched.get("ok") or not wait:
            return launched
        ready = until_stopped(window_view.wait_uefn_ready, root=str(path), timeout=timeout)
        result = {**launched, "ok": bool(ready.get("ok")), "ready": ready}
        if not result["ok"]:
            result["error"] = ready.get("error") or "UEFN did not become ready"
        return result
