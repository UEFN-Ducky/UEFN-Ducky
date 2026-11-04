"""Project-scoped chat folders and conversations: the old import path of ``frontend.ui_web.chats``.

The code lives in ``chats/`` (store, saves, conversations, folders, groups, lanes). Every
name is read from the chats module that owns it. Setting a name here (a test's
monkeypatch) sets it on that module, so callers inside the package see it too.
"""

from __future__ import annotations

import sys as _sys
import types as _types

from frontend.ui_web.chats import conversations as _conversations
from frontend.ui_web.chats import folders as _folders
from frontend.ui_web.chats import groups as _groups
from frontend.ui_web.chats import lanes as _lanes
from frontend.ui_web.chats import saves as _saves
from frontend.ui_web.chats import store as _store

_PARTS = (_store, _saves, _conversations, _folders, _groups, _lanes)


def _homes(name: str) -> list[_types.ModuleType]:
    return [part for part in _PARTS if name in vars(part)]


class _Shim(_types.ModuleType):
    def __getattr__(self, name: str):
        for part in _PARTS:
            found = vars(part)
            if name in found:
                return found[name]
        raise AttributeError(f"module {self.__name__!r} has no attribute {name!r}")

    def __setattr__(self, name: str, value) -> None:
        homes = [] if name.startswith("__") else _homes(name)
        if not homes:
            super().__setattr__(name, value)
            return
        for part in homes:
            setattr(part, name, value)

    def __delattr__(self, name: str) -> None:
        homes = [] if name.startswith("__") else _homes(name)
        if not homes:
            super().__delattr__(name)
            return
        for part in homes:
            delattr(part, name)


_sys.modules[__name__].__class__ = _Shim
