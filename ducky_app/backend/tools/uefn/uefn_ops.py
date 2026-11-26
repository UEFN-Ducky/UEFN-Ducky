"""Launch, close and restart UEFN: ``frontend.window_view`` on Windows, Proton on Linux.
On macOS window_view answers "run it on your PC" (``uefn_on_pc``); proton is never imported.

Looked up on every call, so a test that patches either module still sees its fake.
"""

from __future__ import annotations

import sys
from typing import Any


def _impl() -> Any:
    if sys.platform.startswith("linux"):
        from backend.tools.uefn import proton

        return proton
    from frontend import window_view

    return window_view


def launch_uefn_project(*args: Any, **kwargs: Any) -> Any:
    return _impl().launch_uefn_project(*args, **kwargs)


def close_uefn(*args: Any, **kwargs: Any) -> Any:
    return _impl().close_uefn(*args, **kwargs)


def restart_uefn_project(*args: Any, **kwargs: Any) -> Any:
    return _impl().restart_uefn_project(*args, **kwargs)
