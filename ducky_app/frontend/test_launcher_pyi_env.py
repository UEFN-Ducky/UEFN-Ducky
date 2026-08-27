"""PyInstaller boot leftovers must not leak to child processes.

Run: py -m frontend.test_launcher_pyi_env  (from ducky_app/)
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


def test_cancel_scope_runtimeerror_is_stdio_disconnect() -> None:
    from frontend.launcher import _is_stdio_disconnect

    err = RuntimeError(
        "Attempted to exit a cancel scope that isn't the current tasks's current cancel scope"
    )
    assert _is_stdio_disconnect(err)
    inner = ExceptionGroup("unhandled errors in a TaskGroup (1 sub-exception)", [err])
    mid = ExceptionGroup("unhandled errors in a TaskGroup (1 sub-exception)", [inner])
    outer = ExceptionGroup("unhandled errors in a TaskGroup (1 sub-exception)", [mid])
    assert _is_stdio_disconnect(outer)
    assert not _is_stdio_disconnect(RuntimeError("disk full"))


def main() -> None:
    from frontend.launcher import _PYI_BOOT_ENV_KEYS, scrub_pyinstaller_boot_env

    prev = {key: os.environ.get(key) for key in (*_PYI_BOOT_ENV_KEYS, "KEEP_ME")}
    try:
        for key in _PYI_BOOT_ENV_KEYS:
            os.environ[key] = f"fake-{key}"
        os.environ["KEEP_ME"] = "keep"
        scrub_pyinstaller_boot_env()
        for key in _PYI_BOOT_ENV_KEYS:
            assert key not in os.environ, f"{key} should be gone"
        assert os.environ.get("KEEP_ME") == "keep"
    finally:
        for key, value in prev.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
    print("ok")


if __name__ == "__main__":
    here = Path(__file__).resolve().parent
    ducky_app = here.parent
    root = ducky_app.parent
    for p in (str(root), str(ducky_app)):
        if p not in sys.path:
            sys.path.insert(0, p)
    main()
