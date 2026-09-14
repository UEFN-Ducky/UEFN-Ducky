"""Plugin node handlers + emit (disabled plugin → handler gone)."""

from __future__ import annotations

from typing import Any, Callable

_HANDLERS: dict[str, tuple[str, Callable[..., Any]]] = {}


def register_node(plugin_id: str, node_type: str, handler: Callable[..., Any]) -> None:
    ntype = (node_type or "").strip()
    pid = (plugin_id or "").strip()
    if not ntype or not pid or not callable(handler):
        return
    _HANDLERS[ntype] = (pid, handler)


def clear_for_plugin(plugin_id: str) -> None:
    pid = (plugin_id or "").strip()
    for ntype, (owner, _) in list(_HANDLERS.items()):
        if owner == pid:
            _HANDLERS.pop(ntype, None)


def clear_all() -> None:
    _HANDLERS.clear()


def get_handler(node_type: str) -> Callable[..., Any] | None:
    row = _HANDLERS.get((node_type or "").strip())
    if row is None:
        return None
    pid, fn = row
    try:
        from backend.uefn_plugins.host import is_plugin_enabled

        if not is_plugin_enabled(pid):
            return None
    except Exception:
        return None
    return fn


def handler_plugin_id(node_type: str) -> str:
    row = _HANDLERS.get((node_type or "").strip())
    return row[0] if row else ""
