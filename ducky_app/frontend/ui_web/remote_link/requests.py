"""At-least-once, run-once remote requests.

A viewer gives each request an id (``X-Ducky-Request-Id`` or ``request_id``)
and sends it again when the link dropped before the answer came. The ledger
runs the call the first time; a resend while it still runs waits for that same
run, and a resend after it finished gets the stored answer. Nothing runs twice.
"""

from __future__ import annotations

import re
import threading
import time
from collections import OrderedDict
from typing import Any, Callable

KEEP_S = 300.0
MAX_KEPT = 4096
_ID_RE = re.compile(r"^[A-Za-z0-9._:-]{1,96}$")


def clean_request_id(raw: Any) -> str:
    rid = str(raw or "").strip()
    return rid if _ID_RE.match(rid) else ""


class _Entry:
    __slots__ = ("done", "value", "error", "at")

    def __init__(self) -> None:
        self.done = threading.Event()
        self.value: Any = None
        self.error: BaseException | None = None
        self.at = 0.0


class RequestLedger:
    def __init__(self, *, keep_s: float = KEEP_S, max_kept: int = MAX_KEPT, clock: Callable[[], float] = time.monotonic) -> None:
        self._keep_s = keep_s
        self._max = max_kept
        self._clock = clock
        self._lock = threading.Lock()
        self._rows: OrderedDict[str, _Entry] = OrderedDict()
        self.runs = 0
        self.replays = 0

    def _prune(self, now: float) -> None:
        while self._rows:
            key, row = next(iter(self._rows.items()))
            if len(self._rows) > self._max or (row.done.is_set() and now - row.at > self._keep_s):
                self._rows.pop(key)
                continue
            break

    def run(self, request_id: str, fn: Callable[[], Any], *, wait_s: float = 60.0) -> tuple[Any, bool]:
        """``(result, replayed)``. ``replayed`` is True when the call did not run again."""
        rid = clean_request_id(request_id)
        if not rid:
            return fn(), False
        with self._lock:
            now = self._clock()
            self._prune(now)
            row = self._rows.get(rid)
            owner = row is None
            if owner:
                row = self._rows[rid] = _Entry()
                self.runs += 1
            else:
                self.replays += 1
        if not owner:
            if not row.done.wait(wait_s):
                raise TimeoutError("request still running")
            if row.error is not None:
                raise row.error
            return row.value, True
        try:
            row.value = fn()
        except BaseException as exc:
            row.error = exc
            # A failed call may be tried again for real.
            with self._lock:
                self._rows.pop(rid, None)
            raise
        finally:
            row.at = self._clock()
            row.done.set()
        return row.value, False


_ledger = RequestLedger()


def ledger() -> RequestLedger:
    return _ledger


def call_once(header_id: Any, payload: Any, fn: Callable[[], Any]) -> tuple[int, dict[str, Any]]:
    """``(status, reply)`` for one ``/__panel_api`` call, run once per request id.

    The id comes from the ``X-Ducky-Request-Id`` header or the body's ``request_id``.
    An unknown method (AttributeError) is 404; any other failure is a 200 with ``ok: false``.
    """
    body_id = payload.get("request_id") if isinstance(payload, dict) else ""
    rid = clean_request_id(header_id or body_id)
    try:
        result, replayed = _ledger.run(rid, fn)
    except AttributeError:
        return 404, {"ok": False, "error": "method not allowed"}
    except Exception as exc:
        return 200, {"ok": False, "error": str(exc)}
    reply: dict[str, Any] = {"ok": True, "result": result}
    if rid:
        reply.update(request_id=rid, replayed=replayed)
    return 200, reply
