"""JSON-lines protocol between Ducky-Launcher.exe and ``UEFN-Ducky.exe --backend``.

One JSON object per line, UTF-8, at most 1 MB a line.

backend -> launcher (stdout):
    {"t":"hello","protocol":1,"app_version":"1.2.x","port":N,"origin":"http://127.0.0.1:N","profile_dir":"...","pid":N}
    {"t":"result","id":"...","ok":true,"value":...}
    {"t":"result","id":"...","ok":false,"error":"..."}
    {"t":"event","name":"toast|title|quit","data":{...}}
launcher -> backend (stdin):
    {"t":"call","id":"...","name":"...","args":[...]}
    {"t":"bye","reason":"..."}            (stdin EOF also means shut down)

Unknown fields are ignored on both sides. A peer protocol outside [MIN, MAX] is a mismatch.
"""

from __future__ import annotations

import json
from typing import Any

PROTOCOL_VERSION = 1
MIN_PROTOCOL = 1
MAX_PROTOCOL = 1
MAX_LINE_BYTES = 1024 * 1024  # one line, without its newline

EVENT_NAMES = ("toast", "title", "quit")

# Launcher exit codes (anything but 0 means the python window takes over).
EXIT_OK = 0
EXIT_WEBVIEW2_MISSING = 20
EXIT_PROTOCOL_MISMATCH = 21
EXIT_CRASH_LOOP = 22
EXIT_NO_HELLO = 23
EXIT_START_ERROR = 24

_MISSING = object()


class ProtocolError(ValueError):
    """A line that is not a valid message of this protocol."""


class ProtocolMismatch(ProtocolError):
    """The peer speaks a protocol version outside [MIN_PROTOCOL, MAX_PROTOCOL]."""

    def __init__(self, peer: object) -> None:
        super().__init__(f"protocol mismatch: peer {peer!r}, supported [{MIN_PROTOCOL},{MAX_PROTOCOL}]")
        self.peer = peer


def _is_int(v: object) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def _is_str(v: object) -> bool:
    return isinstance(v, str)


def _any(_v: object) -> bool:
    return True


# t -> ((field, check, required), ...). Only these fields survive decode().
_SCHEMA: dict[str, tuple[tuple[str, Any, bool], ...]] = {
    "hello": (
        ("protocol", _is_int, True),
        ("app_version", _is_str, True),
        ("port", _is_int, True),
        ("origin", _is_str, True),
        ("profile_dir", _is_str, True),
        ("pid", _is_int, True),
    ),
    "result": (
        ("id", _is_str, True),
        ("ok", lambda v: isinstance(v, bool), True),
        ("value", _any, False),
        ("error", _is_str, False),
    ),
    "event": (
        ("name", lambda v: isinstance(v, str) and bool(v), True),
        ("data", lambda v: isinstance(v, dict), False),
    ),
    "call": (
        ("id", _is_str, True),
        ("name", lambda v: isinstance(v, str) and bool(v), True),
        ("args", lambda v: isinstance(v, list), False),
    ),
    "bye": (("reason", _is_str, False),),
}

# Defaults filled in for optional fields, so readers never need .get().
_DEFAULTS: dict[str, dict[str, Any]] = {
    "event": {"data": {}},
    "call": {"args": []},
    "bye": {"reason": ""},
}


def _validate(obj: object) -> dict[str, Any]:
    if not isinstance(obj, dict):
        raise ProtocolError("message is not a JSON object")
    t = obj.get("t")
    schema = _SCHEMA.get(t) if isinstance(t, str) else None
    if schema is None:
        raise ProtocolError(f"unknown message type {t!r}")
    out: dict[str, Any] = {"t": t}
    for field, check, required in schema:
        value = obj.get(field, _MISSING)
        if value is _MISSING:
            if required:
                raise ProtocolError(f"{t}: missing {field!r}")
            if field in _DEFAULTS.get(t, {}):
                out[field] = _DEFAULTS[t][field]
            continue
        if not check(value):
            raise ProtocolError(f"{t}: bad {field!r}: {value!r}")
        out[field] = value
    if t == "result":
        if out["ok"]:
            out.setdefault("value", None)
            out.pop("error", None)
        else:
            if "error" not in out:
                raise ProtocolError("result: ok=false needs 'error'")
            out.pop("value", None)
    return out


def encode(msg: dict[str, Any]) -> str:
    """One protocol line, newline included. Raises ProtocolError (bad or over 1 MB)."""
    clean = _validate(msg)
    line = json.dumps(clean, ensure_ascii=False, separators=(",", ":"))
    if len(line.encode("utf-8")) > MAX_LINE_BYTES:
        raise ProtocolError(f"line over {MAX_LINE_BYTES} bytes")
    return line + "\n"


def decode(line: str | bytes) -> dict[str, Any]:
    """The message on one line, unknown fields dropped. Raises ProtocolError."""
    if isinstance(line, str):
        raw = line.encode("utf-8")
    else:
        raw = bytes(line)
    raw = raw.rstrip(b"\r\n")
    if len(raw) > MAX_LINE_BYTES:
        raise ProtocolError(f"line over {MAX_LINE_BYTES} bytes")
    try:
        obj = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise ProtocolError(f"not JSON: {exc}") from None
    return _validate(obj)


def negotiate(peer: dict[str, Any] | int) -> int:
    """The protocol both sides speak, given the peer's hello (or its version). Raises ProtocolMismatch."""
    version = peer.get("protocol") if isinstance(peer, dict) else peer
    if not _is_int(version) or not (MIN_PROTOCOL <= version <= MAX_PROTOCOL):
        raise ProtocolMismatch(version)
    return min(version, PROTOCOL_VERSION)


# --- builders ---------------------------------------------------------------------


def hello(*, app_version: str, port: int, profile_dir: str, pid: int) -> dict[str, Any]:
    return {
        "t": "hello",
        "protocol": PROTOCOL_VERSION,
        "app_version": app_version,
        "port": port,
        "origin": f"http://127.0.0.1:{port}",
        "profile_dir": profile_dir,
        "pid": pid,
    }


def result_ok(call_id: str, value: Any = None) -> dict[str, Any]:
    return {"t": "result", "id": call_id, "ok": True, "value": value}


def result_error(call_id: str, error: str) -> dict[str, Any]:
    return {"t": "result", "id": call_id, "ok": False, "error": error}


def event(name: str, data: dict[str, Any] | None = None) -> dict[str, Any]:
    return {"t": "event", "name": name, "data": dict(data or {})}


def call(call_id: str, name: str, args: list[Any] | None = None) -> dict[str, Any]:
    return {"t": "call", "id": call_id, "name": name, "args": list(args or [])}


def bye(reason: str = "") -> dict[str, Any]:
    return {"t": "bye", "reason": reason}
