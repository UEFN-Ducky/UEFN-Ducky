"""The same panel call against the Python backend and the Rust one (Ducky-Backend).

A call moves to Rust only when a test gets the same ``(status, reply)`` from both.
``PythonBackend`` runs the call the way the ``/__panel_api/<method>`` route does, in this
process; ``RustBackend`` posts it to a Ducky-Backend it started. Build that with
``cargo build -p backend`` in native_launcher/, or point DUCKY_BACKEND_EXE at one.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

_REPO = Path(__file__).resolve().parents[3]
_EXE = "Ducky-Backend.exe" if sys.platform == "win32" else "Ducky-Backend"

Answer = tuple[int, Any]


def backend_exe() -> Path | None:
    """DUCKY_BACKEND_EXE, else the newest Ducky-Backend under native_launcher/target."""
    pinned = os.environ.get("DUCKY_BACKEND_EXE")
    if pinned:
        return Path(pinned) if Path(pinned).is_file() else None
    built = [p for p in (_REPO / "native_launcher" / "target").glob(f"*/{_EXE}") if p.is_file()]
    return max(built, key=lambda p: p.stat().st_mtime, default=None)


class PythonBackend:
    name = "python"

    def __init__(self) -> None:
        from frontend.ui_web.panel_api import PanelApi

        self.api = PanelApi.__new__(PanelApi)  # no __init__: no plugins, models or window

    def call(self, method: str, *args: Any) -> Answer:
        from frontend.duckyos_account import call_panel_method
        from frontend.ui_web.remote_link.requests import call_once

        return call_once(None, {"args": list(args)}, lambda: call_panel_method(self.api, method, list(args)))


class RustBackend:
    name = "rust"

    def __init__(self, exe: Path) -> None:
        self.proc = subprocess.Popen([str(exe)], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
        assert self.proc.stdout is not None
        self.origin = json.loads(self.proc.stdout.readline())["origin"]

    def request(self, path: str, body: Any = None) -> Answer:
        data = None if body is None else json.dumps(body).encode()
        req = urllib.request.Request(self.origin + path, data=data, headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return resp.status, json.loads(resp.read() or b"null")
        except urllib.error.HTTPError as err:
            raw = err.read()
            try:
                return err.code, json.loads(raw)
            except ValueError:
                return err.code, raw.decode(errors="replace")

    def call(self, method: str, *args: Any) -> Answer:
        return self.request(f"/__panel_api/{method}", {"args": list(args)})

    def store(self, repo: str, function: str, **args: Any) -> Any:
        """One repo function of the Rust store (``/__rs/store/<repo>/<function>``)."""
        status, reply = self.request(f"/__rs/store/{repo}/{function}", args)
        assert status == 200 and reply.get("ok") is True, f"{repo}.{function}{args}: {status} {reply}"
        return reply["result"]

    def close(self) -> None:
        if self.proc.stdin is not None:
            self.proc.stdin.close()  # the backend's signal to quit
        self.proc.wait(timeout=10)


def plain(value: Any) -> Any:
    """A Python result as it looks after JSON: tuples are lists, sets are sorted lists."""
    return json.loads(json.dumps(value, default=sorted))


def _without(answer: Answer, ignore: tuple[str, ...]) -> Answer:
    status, reply = answer
    result = reply.get("result") if isinstance(reply, dict) else None
    if ignore and isinstance(result, dict):
        reply = {**reply, "result": {k: v for k, v in result.items() if k not in ignore}}
    return status, reply


def check_same(python: Any, rust: Any, method: str, *args: Any, ignore: tuple[str, ...] = ()) -> Answer:
    """Both backends' answers to one call, equal but for ``ignore`` (top-level result keys
    that differ by nature: timestamps, ids). Returns the Python answer."""
    theirs = _without(python.call(method, *args), ignore)
    ours = _without(rust.call(method, *args), ignore)
    assert ours == theirs, f"{method}{args}: rust {ours} != python {theirs}"
    return theirs
