"""Command name → handler (runs on main thread)."""

import inspect
from typing import Callable, Dict

_HANDLERS: Dict[str, Callable] = {}


def register(name: str):
    def decorator(fn: Callable):
        _HANDLERS[name] = fn
        return fn
    return decorator


def _coerce_params(handler: Callable, params: dict) -> dict:
    """Drop unknown keys so older MCP clients do not crash the listener."""
    accepted = set(inspect.signature(handler).parameters)
    return {k: v for k, v in (params or {}).items() if k in accepted}


def dispatch(command: str, params: dict) -> dict:
    handler = _HANDLERS.get(command)
    if handler is None:
        raise ValueError(f"Unknown command: {command}. Available: {list(_HANDLERS.keys())}")
    return handler(**_coerce_params(handler, params))


def handler_names():
    return list(_HANDLERS.keys())
