"""Keep ``backend.uefn_plugins.host`` working while its code lives in plugin_host modules.

The host was one module, so code and tests read and patch its names as ``host.X``. After
the split, ``X`` is bound in the module that defines it and in every module that imports
it. :func:`install` turns ``host`` into a forwarding module:

- reading ``host.X`` (or ``from host import X``) gives the value of the module that owns
  it now, so a flag that module rebinds (``_LOADED``) is never a stale copy;
- setting ``host.X`` (``monkeypatch.setattr`` / ``mock.patch.object``) sets it in every
  plugin_host module that binds ``X``, the same as patching the one module did;
- deleting ``host.X`` hides it until it is set again, so ``mock.patch.object`` puts back
  the live value it read when the patch began.
"""

from __future__ import annotations

import sys
import types
from typing import Any, Iterable

_MODULES = "_forward_modules_"
_HIDDEN = "_forward_hidden_"


def _binders(modules: Iterable[types.ModuleType], name: str) -> list[types.ModuleType]:
    return [mod for mod in modules if name in vars(mod)]


class _ForwardingModule(types.ModuleType):
    def __getattr__(self, name: str) -> Any:
        own = self.__dict__
        if name not in own.get(_HIDDEN, ()):
            for mod in own.get(_MODULES, ()):
                found = vars(mod)
                if name in found:
                    return found[name]
        raise AttributeError(f"module {self.__name__!r} has no attribute {name!r}")

    def __setattr__(self, name: str, value: Any) -> None:
        own = self.__dict__
        own.get(_HIDDEN, set()).discard(name)
        binders = _binders(own.get(_MODULES, ()), name)
        for mod in binders:
            setattr(mod, name, value)
        if name in own or not binders:
            super().__setattr__(name, value)

    def __delattr__(self, name: str) -> None:
        own = self.__dict__
        if name in own:
            super().__delattr__(name)
        elif _binders(own.get(_MODULES, ()), name) and name not in own.get(_HIDDEN, ()):
            own.setdefault(_HIDDEN, set()).add(name)
        else:
            raise AttributeError(name)

    def __dir__(self) -> list[str]:
        names = set(self.__dict__)
        for mod in self.__dict__.get(_MODULES, ()):
            names.update(vars(mod))
        return sorted(names - set(self.__dict__.get(_HIDDEN, ())))


def install(module_name: str, modules: Iterable[types.ModuleType]) -> None:
    """Forward ``module_name``'s missing and patched attributes to ``modules``."""
    module = sys.modules[module_name]
    module.__dict__[_MODULES] = tuple(modules)
    module.__dict__.setdefault(_HIDDEN, set())
    module.__class__ = _ForwardingModule
