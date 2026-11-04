"""For tests: swap a name in every agent_turns module that holds it.

The parts import each other's names (``routing`` does ``from .state import is_agent_running``),
so each holds its own copy. A test that swaps a name in one part only leaves the others
calling the old one; these helpers swap every copy.
"""

from __future__ import annotations

from contextlib import ExitStack, contextmanager
from types import ModuleType
from typing import Any, Iterator

from frontend.ui_web.agent_turns import errors, pending, push, routing, run, state

PARTS: tuple[ModuleType, ...] = (push, errors, state, pending, run, routing)


def holders(name: str) -> list[ModuleType]:
    """Every agent_turns module that binds ``name``."""
    found = [part for part in PARTS if name in vars(part)]
    if not found:
        raise AttributeError(f"no agent_turns module binds {name!r}")
    return found


def patch_turns(monkeypatch: Any, name: str, value: Any) -> None:
    """``monkeypatch.setattr`` ``name`` to ``value`` in every module that holds it."""
    for part in holders(name):
        monkeypatch.setattr(part, name, value)


@contextmanager
def turns_patched(name: str, new: Any = None, **mock_kwargs: Any) -> Iterator[Any]:
    """``mock.patch.object`` for every holder of ``name``, all with one value (``new``, or
    one MagicMock made from ``mock_kwargs``). Yields that value."""
    from unittest import mock

    value = new if new is not None else mock.MagicMock(**mock_kwargs)
    with ExitStack() as stack:
        for part in holders(name):
            stack.enter_context(mock.patch.object(part, name, value))
        yield value
