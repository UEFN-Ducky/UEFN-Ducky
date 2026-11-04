"""Ask / Plan / Agent mode dispatch for the React panel (ui_web only).

The code lives in ``frontend.ui_web.agent_turns``; this is its old import path:

- ``push``: the panel's push handler, the active chat, bridge → panel forwarding
- ``errors``: crash records, the crashed turn's row, auto-resume, the plan-limit hand-off
- ``state``: sessions, run admission, which chats run, stopping a turn, run bookkeeping
- ``pending``: sends that wait (no model, a busy chat) and their delivery
- ``run``: the embedded AgentRunner turn loop
- ``routing``: ``run_message`` / ``run_message_and_wait`` and the mode suffixes
"""

from __future__ import annotations

import sys as _sys
import types as _types
from typing import Any

from frontend.ui_web.agent_turns import errors as _errors_part
from frontend.ui_web.agent_turns import pending as _pending_part
from frontend.ui_web.agent_turns import push as _push_part
from frontend.ui_web.agent_turns import routing as _routing_part
from frontend.ui_web.agent_turns import run as _run_part
from frontend.ui_web.agent_turns import state as _state_part

# Callers and tests still read and patch ``agent_modes.<name>``. A patch here is applied to
# every agent_turns module that holds that name, so the moved code sees it exactly as it
# did when it all lived in this one module.
_PARTS: tuple[_types.ModuleType, ...] = (
    _push_part, _errors_part, _state_part, _pending_part, _run_part, _routing_part,
)
# Rebound inside their own module (``global``): always read live, never copied here.
_LIVE = frozenset({"_panel_push", "_forward_started", "_panel_runs_cache"})


class _AgentModesShim(_types.ModuleType):
    def __getattr__(self, name: str) -> Any:
        for part in _PARTS:
            if name in vars(part):
                return vars(part)[name]
        raise AttributeError(f"module {self.__name__!r} has no attribute {name!r}")

    def __setattr__(self, name: str, value: Any) -> None:
        for part in _PARTS:
            if name in vars(part):
                setattr(part, name, value)
        if name not in _LIVE:
            super().__setattr__(name, value)

    def __delattr__(self, name: str) -> None:
        for part in _PARTS:
            if name in vars(part):
                delattr(part, name)
        if name in self.__dict__:
            super().__delattr__(name)


for _part in _PARTS:
    for _name, _value in vars(_part).items():
        if not _name.startswith("__") and _name not in _LIVE:
            globals().setdefault(_name, _value)
del _part, _name, _value
_sys.modules[__name__].__class__ = _AgentModesShim
