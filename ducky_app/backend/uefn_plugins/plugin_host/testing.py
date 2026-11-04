"""For tests: swap a name in every plugin_host module that holds it.

The modules import each other's names (``gateways`` does ``from .state import _LLM_PROVIDERS``,
``ui_registry`` does ``from .boot import ensure_plugins_loaded``), so each holds its own copy.
A test that swaps a name in one module only leaves the others calling the old one; these
helpers swap every copy.
"""

from __future__ import annotations

from contextlib import ExitStack, contextmanager
from types import ModuleType
from typing import Any, Iterator

from backend.uefn_plugins.plugin_host import (
    api,
    backends,
    boot,
    contributions,
    crash_guard,
    errors,
    folders,
    gateways,
    lifecycle,
    loader,
    panel_rpc,
    services,
    state,
    tool_registry,
    ui_registry,
)

MODULES: tuple[ModuleType, ...] = (
    state,
    boot,
    loader,
    lifecycle,
    backends,
    crash_guard,
    contributions,
    ui_registry,
    services,
    gateways,
    tool_registry,
    panel_rpc,
    folders,
    errors,
    api,
)


def holders(name: str) -> list[ModuleType]:
    """Every plugin_host module that binds ``name``."""
    found = [mod for mod in MODULES if name in vars(mod)]
    if not found:
        raise AttributeError(f"no plugin_host module binds {name!r}")
    return found


def patch_host(monkeypatch: Any, name: str, value: Any, raising: bool = True) -> None:
    """``monkeypatch.setattr`` ``name`` to ``value`` in every module that holds it."""
    try:
        found = holders(name)
    except AttributeError:
        if raising:
            raise
        found = [state]
    for mod in found:
        monkeypatch.setattr(mod, name, value, raising=raising)


@contextmanager
def host_patched(name: str, new: Any = None, **mock_kwargs: Any) -> Iterator[Any]:
    """``mock.patch.object`` for every holder of ``name``, all with one value (``new``, or
    one MagicMock made from ``mock_kwargs``). Yields that value."""
    from unittest import mock

    value = new if new is not None else mock.MagicMock(**mock_kwargs)
    with ExitStack() as stack:
        for mod in holders(name):
            stack.enter_context(mock.patch.object(mod, name, value))
        yield value
