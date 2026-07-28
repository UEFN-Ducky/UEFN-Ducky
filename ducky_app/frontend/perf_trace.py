"""Always-on, low-overhead performance tracing for the Ducky panel.

Writes to ``%LOCALAPPDATA%/UEFN-Ducky/perf/``:
  - ``session-<YYYYMMDD-HHMMSS>-<pid>.jsonl`` — slow/notable events
  - ``session-<...>-report.json`` — aggregated summary (rewritten ~every 15s)
  - ``latest-report.json`` — copy of the current session's report

Exceptions are swallowed everywhere; a bad tracer must never break the app.
"""

from __future__ import annotations

import atexit
import json
import os
import statistics
import threading
import time
from pathlib import Path
from typing import Any

RING_SIZE = 2000
MAX_SESSIONS = 10
REPORT_INTERVAL_SEC = 15.0

# Persist to jsonl when duration or queue depth crosses these (still always ring).
SLOW_MS = {
    "ui_js": 16.0,
    "push_flush": 25.0,
    "tool_push": 0.0,  # always log size meta when result_bytes set
    "conv_save": 50.0,
    "listener_cmd": 100.0,
    "ui_stall": 0.0,
    "ui_frame": 0.0,
    "ui_js_stall": 0.0,  # watchdog thread-stack dumps — always persist
    "boot": 0.0,  # cold-start phases — always persist so splash wait is measurable
}
QUEUE_DEPTH_NOTABLE = 8
PAYLOAD_BYTES_NOTABLE = 32_768

_lock = threading.Lock()
_ring: list[dict[str, Any]] = []
_session_id = ""
_session_jsonl: Path | None = None
_session_report: Path | None = None
_latest_report: Path | None = None
_started = False
_writer_thread: threading.Thread | None = None
_stop_writer = threading.Event()


def _perf_dir() -> Path:
    try:
        from frontend.settings import default_app_data_dir

        return default_app_data_dir() / "perf"
    except Exception:
        base = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA") or str(Path.home())
        return Path(base) / "UEFN-Ducky" / "perf"


def _percentile(sorted_vals: list[float], p: float) -> float:
    if not sorted_vals:
        return 0.0
    if len(sorted_vals) == 1:
        return float(sorted_vals[0])
    k = (len(sorted_vals) - 1) * (p / 100.0)
    f = int(k)
    c = min(f + 1, len(sorted_vals) - 1)
    if f == c:
        return float(sorted_vals[f])
    return float(sorted_vals[f] + (sorted_vals[c] - sorted_vals[f]) * (k - f))


def _should_persist(kind: str, duration_ms: float, meta: dict[str, Any]) -> bool:
    threshold = SLOW_MS.get(kind, 50.0)
    if duration_ms >= threshold:
        return True
    if int(meta.get("queue_depth") or 0) >= QUEUE_DEPTH_NOTABLE:
        return True
    if int(meta.get("payload_bytes") or meta.get("js_bytes") or meta.get("result_bytes") or 0) >= PAYLOAD_BYTES_NOTABLE:
        return True
    if int(meta.get("event_count") or 0) >= 50:
        return True
    if float(meta.get("oldest_event_age_ms") or 0) >= 100.0:
        return True
    return False


def ensure_started() -> None:
    """Idempotent: open session files and start the periodic report writer."""
    global _started, _session_id, _session_jsonl, _session_report, _latest_report, _writer_thread
    with _lock:
        if _started:
            return
        try:
            d = _perf_dir()
            d.mkdir(parents=True, exist_ok=True)
            stamp = time.strftime("%Y%m%d-%H%M%S")
            _session_id = f"session-{stamp}-{os.getpid()}"
            _session_jsonl = d / f"{_session_id}.jsonl"
            _session_report = d / f"{_session_id}-report.json"
            _latest_report = d / "latest-report.json"
            _prune_old_sessions(d)
            _started = True
        except Exception:
            _started = True  # don't retry forever on path failures
            return
    atexit.register(_write_report_safe)
    _stop_writer.clear()
    _writer_thread = threading.Thread(target=_report_loop, name="perf-report", daemon=True)
    _writer_thread.start()


def _prune_old_sessions(d: Path) -> None:
    try:
        reports = sorted(d.glob("session-*-report.json"), key=lambda p: p.stat().st_mtime, reverse=True)
        keep = {p.name for p in reports[:MAX_SESSIONS]}
        keep |= {p.name.replace("-report.json", ".jsonl") for p in reports[:MAX_SESSIONS]}
        for p in d.iterdir():
            if p.name == "latest-report.json":
                continue
            if p.name.startswith("session-") and p.name not in keep:
                try:
                    p.unlink()
                except OSError:
                    pass
    except Exception:
        pass


def _report_loop() -> None:
    while not _stop_writer.wait(REPORT_INTERVAL_SEC):
        _write_report_safe()


def _write_report_safe() -> None:
    try:
        write_report()
    except Exception:
        pass


def trace(kind: str, name: str = "", duration_ms: float = 0.0, **meta: Any) -> None:
    """Record one event. Always rings; persists when notable."""
    try:
        ensure_started()
        entry: dict[str, Any] = {
            "ts": time.time(),
            "kind": kind,
            "name": name or kind,
            "duration_ms": round(float(duration_ms), 3),
        }
        for k, v in meta.items():
            if v is None:
                continue
            entry[k] = v
        persist = _should_persist(kind, float(duration_ms), meta)
        with _lock:
            _ring.append(entry)
            if len(_ring) > RING_SIZE:
                del _ring[: len(_ring) - RING_SIZE]
            path = _session_jsonl if persist else None
        if path is not None:
            try:
                with path.open("a", encoding="utf-8") as f:
                    f.write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")
            except OSError:
                pass
    except Exception:
        pass


def clear() -> None:
    """Reset the in-memory ring (session files stay)."""
    with _lock:
        _ring.clear()


def summary(*, top_n: int = 25) -> dict[str, Any]:
    """Aggregate the in-memory ring into a report dict."""
    ensure_started()
    with _lock:
        entries = list(_ring)
        session_id = _session_id
        jsonl = str(_session_jsonl) if _session_jsonl else ""
        report_path = str(_session_report) if _session_report else ""
        latest = str(_latest_report) if _latest_report else ""

    by_kind: dict[str, list[dict[str, Any]]] = {}
    for e in entries:
        by_kind.setdefault(str(e.get("kind") or "unknown"), []).append(e)

    kinds: dict[str, Any] = {}
    for kind, items in by_kind.items():
        durations = sorted(float(i.get("duration_ms") or 0) for i in items)
        kinds[kind] = {
            "count": len(items),
            "p50_ms": round(_percentile(durations, 50), 2),
            "p95_ms": round(_percentile(durations, 95), 2),
            "max_ms": round(max(durations) if durations else 0.0, 2),
            "mean_ms": round(statistics.fmean(durations), 2) if durations else 0.0,
        }

    def _score(e: dict[str, Any]) -> float:
        d = float(e.get("duration_ms") or 0)
        bytes_ = float(
            e.get("js_bytes") or e.get("payload_bytes") or e.get("result_bytes") or e.get("bytes") or 0
        )
        age = float(e.get("oldest_event_age_ms") or 0)
        depth = float(e.get("queue_depth") or 0)
        return d + (bytes_ / 10_000.0) + age + depth * 5

    slowest = sorted(entries, key=_score, reverse=True)[:top_n]
    ui_stalls = [e for e in entries if e.get("kind") in ("ui_stall", "ui_frame")]

    return {
        "ok": True,
        "session_id": session_id,
        "generated_at": time.time(),
        "generated_at_iso": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "ring_size": len(entries),
        "paths": {
            "jsonl": jsonl,
            "session_report": report_path,
            "latest_report": latest,
            "perf_dir": str(_perf_dir()),
        },
        "kinds": kinds,
        "ui_stall_count": len(ui_stalls),
        "slowest": slowest,
        "hints": _hints(kinds, slowest),
    }


def _hints(kinds: dict[str, Any], slowest: list[dict[str, Any]]) -> list[str]:
    out: list[str] = []
    ui = kinds.get("ui_js") or {}
    if float(ui.get("p95_ms") or 0) >= 50:
        out.append("ui_js p95 is high — WebView2 evaluate_js is stalling (UI thread busy or huge payloads).")
    for e in slowest[:5]:
        if int(e.get("js_bytes") or e.get("payload_bytes") or e.get("result_bytes") or 0) >= PAYLOAD_BYTES_NOTABLE:
            out.append(
                f"Large payload on {e.get('kind')}/{e.get('name')}: "
                f"{e.get('js_bytes') or e.get('payload_bytes') or e.get('result_bytes')} bytes."
            )
            break
    push = kinds.get("push_flush") or {}
    if float(push.get("max_ms") or 0) >= 100:
        out.append("push_flush max is high — agent event batcher backlog.")
    save = kinds.get("conv_save") or {}
    if float(save.get("p95_ms") or 0) >= 100:
        out.append("conv_save is slow — large chat JSON writes on disk.")
    listener = kinds.get("listener_cmd") or {}
    if float(listener.get("p95_ms") or 0) >= 500:
        out.append("listener_cmd p95 is high — UEFN main-thread commands blocking the agent.")
    if not out and kinds:
        out.append("No strong stall signal yet — reproduce lag and re-check latest-report.json.")
    return out


def write_report() -> dict[str, Any]:
    """Write session + latest report JSON to disk. Returns the summary."""
    report = summary()
    ensure_started()
    with _lock:
        session_path = _session_report
        latest_path = _latest_report
    payload = json.dumps(report, ensure_ascii=False, indent=2, default=str)
    for path in (session_path, latest_path):
        if path is None:
            continue
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(payload, encoding="utf-8")
        except OSError:
            pass
    return report


def read_latest_report() -> dict[str, Any] | None:
    """Load ``latest-report.json`` from disk (survives process death)."""
    try:
        path = _perf_dir() / "latest-report.json"
        if not path.is_file():
            return None
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
