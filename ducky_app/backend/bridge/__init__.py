"""UEFN listener bridge package.

Implementation lives in submodules (``client``, ``status``, ``serial``, …).
Attribute access is forwarded to ``client`` so ``import backend.bridge`` and
monkeypatches on ``backend.bridge.<public>`` keep working for lazy importers.
"""
from __future__ import annotations

from importlib import import_module
from typing import Any

_client = import_module("backend.bridge.client")


def __getattr__(name: str) -> Any:
    return getattr(_client, name)


def __dir__() -> list[str]:
    return sorted(set(dir(_client)) | {"client", "status", "serial", "plugin_gate", "dynamic_tools"})
