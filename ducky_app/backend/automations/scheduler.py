"""Panel-process cron/interval loop. No-ops when the panel is not running."""

from __future__ import annotations

import logging
import threading
import time
from datetime import datetime
from typing import Any

from backend.automations.runner import run_automation
from backend.automations.store import _all

_log = logging.getLogger("automations")
_LOCK = threading.Lock()
_THREAD: threading.Thread | None = None
_STOP = threading.Event()
_TICK_S = 15.0


def start_scheduler() -> None:
    global _THREAD
    with _LOCK:
        if _THREAD is not None and _THREAD.is_alive():
            return
        _STOP.clear()
        _THREAD = threading.Thread(target=_loop, daemon=True, name="automations-scheduler")
        _THREAD.start()


def stop_scheduler() -> None:
    _STOP.set()


def cron_due(expr: str, now: datetime, last_run: float) -> bool:
    parts = (expr or "").split()
    if len(parts) != 5:
        return False
    if last_run and int(now.timestamp()) // 60 == int(last_run) // 60:
        return False
    minute, hour, dom, month, dow = parts
    py_dow = (now.weekday() + 1) % 7  # cron 0 = Sunday
    return (
        _field_match(minute, now.minute)
        and _field_match(hour, now.hour)
        and _field_match(dom, now.day)
        and _field_match(month, now.month)
        and _field_match(dow, py_dow)
    )


def interval_due(seconds: float, last_run: float, now: float) -> bool:
    if seconds <= 0:
        return False
    if last_run <= 0:
        return True
    return (now - last_run) >= seconds


def _loop() -> None:
    while not _STOP.wait(_TICK_S):
        try:
            _tick()
        except Exception:
            _log.exception("automations scheduler tick failed")


def _tick() -> None:
    now = time.time()
    now_dt = datetime.now()
    for wf in _all():
        if not wf.get("enabled"):
            continue
        last = float(wf.get("last_run") or 0.0)
        for node in (wf.get("graph") or {}).get("nodes") or []:
            if not isinstance(node, dict) or str(node.get("type") or "") != "start.cron":
                continue
            cfg = node.get("config") if isinstance(node.get("config"), dict) else {}
            interval = float(cfg.get("interval_seconds") or 0)
            cron = str(cfg.get("cron") or "").strip()
            due = False
            if interval > 0:
                due = interval_due(interval, last, now)
            elif cron:
                due = cron_due(cron, now_dt, last)
            if due:
                run_automation(str(wf["id"]), starter_id=str(node.get("id") or ""))
                break


def _field_match(pat: str, value: int) -> bool:
    token = (pat or "").strip()
    if token == "*":
        return True
    if token.startswith("*/"):
        try:
            step = int(token[2:])
        except ValueError:
            return False
        return step > 0 and value % step == 0
    if "," in token:
        return any(_field_match(p, value) for p in token.split(","))
    if "-" in token:
        a, _, b = token.partition("-")
        try:
            return int(a) <= value <= int(b)
        except ValueError:
            return False
    try:
        return int(token) == value
    except ValueError:
        return False
