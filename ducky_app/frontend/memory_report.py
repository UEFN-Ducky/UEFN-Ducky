"""What the app process holds, on demand (``ducky_memory_report``, panel ``memory_report``).

- ``process``: RSS, private bytes, peak working set, threads (by name) and Python's
  allocated blocks.
- ``gc``: what a full collection freed, then live gc-tracked objects by type (top N).
- ``containers``: the biggest things the app keeps, by approximate deep size: every
  module global outside the standard library (caches, histories, queues, rings and the
  singletons that own them) and every live thread's frame locals (buffers a loop keeps).
  Sizes are ``sys.getsizeof`` summed over what each one reaches, so an object shared by
  two roots counts in both; ``partial`` marks a walk cut off by its budget.
- ``tracemalloc`` (``start_trace=True`` starts it): the top allocation sites by size, and
  from the second call on what grew since the last call.

Standard library and psutil only: a running app of any version can load this file.
"""

from __future__ import annotations

import collections
import gc
import os
import re
import sys
import threading
import time
import tracemalloc
import types
from typing import Any

TOP = 30
TRACE_TOP = 25
WALK_BUDGET_S = 20.0  # all container walks together
ROOT_MAX_OBJECTS = 2_000_000  # one root's walk stops here (``partial``)
MB = 1024 * 1024

# Never walked into: code and namespaces, not data (a module global pointing at another
# module, a class or a function would otherwise count the whole program).
_SKIP = (types.ModuleType, type, types.FunctionType, types.BuiltinFunctionType, types.MethodType,
         types.CodeType, types.FrameType, types.GetSetDescriptorType, types.MemberDescriptorType,
         types.WrapperDescriptorType, types.MethodDescriptorType, property, classmethod, staticmethod)
_SMALL = (int, float, complex, bool, type(None))
_ATOMS = (str, bytes, bytearray, memoryview, *_SMALL)

_snapshot: list[tracemalloc.Snapshot] = []  # the last call's: the next call diffs against it
_lock = threading.Lock()


def _mb(n: float) -> float:
    return round(float(n) / MB, 1)


def _process() -> dict[str, Any]:
    out: dict[str, Any] = {"pid": os.getpid()}
    try:
        import psutil

        proc = psutil.Process()
        mem = proc.memory_info()
        out.update(rss_mb=_mb(mem.rss), private_mb=_mb(getattr(mem, "private", 0) or 0),
                   peak_wset_mb=_mb(getattr(mem, "peak_wset", 0) or 0), threads=proc.num_threads())
    except Exception as exc:  # noqa: BLE001 — the rest still says plenty
        out["error"] = f"psutil: {exc}"
    names = collections.Counter(re.sub(r"[-_ ]?\d+$", "", t.name) or "?" for t in threading.enumerate())
    out["python_threads"] = len(threading.enumerate())
    out["python_threads_by_name"] = dict(names.most_common(15))
    out["allocated_blocks"] = sys.getallocatedblocks()
    return out


def _native(top: int = 12) -> dict[str, Any]:
    """Memory no Python object accounts for (Windows): committed private regions grouped by
    the allocation they belong to (a heap, a .NET GC segment, a big buffer), the process
    heaps' own totals, and the .NET runtime's GC heap when pythonnet hosts one."""
    out: dict[str, Any] = {}
    if sys.platform != "win32":
        return out
    import ctypes
    from ctypes import wintypes

    class MBI(ctypes.Structure):
        _fields_ = [("BaseAddress", ctypes.c_void_p), ("AllocationBase", ctypes.c_void_p),
                    ("AllocationProtect", wintypes.DWORD), ("PartitionId", wintypes.WORD),
                    ("RegionSize", ctypes.c_size_t), ("State", wintypes.DWORD), ("Protect", wintypes.DWORD),
                    ("Type", wintypes.DWORD)]

    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.VirtualQuery.argtypes = [ctypes.c_void_p, ctypes.POINTER(MBI), ctypes.c_size_t]
    k32.VirtualQuery.restype = ctypes.c_size_t
    mem_commit, mem_private = 0x1000, 0x20000
    by_base: dict[int, int] = {}
    addr, mbi = 0, MBI()
    while k32.VirtualQuery(ctypes.c_void_p(addr), ctypes.byref(mbi), ctypes.sizeof(mbi)):
        if mbi.State == mem_commit and mbi.Type == mem_private:
            base = int(mbi.AllocationBase or 0)
            by_base[base] = by_base.get(base, 0) + int(mbi.RegionSize)
        nxt = int(mbi.BaseAddress or 0) + int(mbi.RegionSize)
        if nxt <= addr:
            break
        addr = nxt
    sizes = sorted(by_base.values(), reverse=True)
    out["private_committed_mb"] = _mb(sum(sizes))
    out["allocations"] = len(sizes)
    out["biggest_allocations_mb"] = [_mb(s) for s in sizes[:top]]
    buckets = {"<1MB": 0, "1-16MB": 0, "16-256MB": 0, ">=256MB": 0}
    for s in sizes:
        key = "<1MB" if s < MB else "1-16MB" if s < 16 * MB else "16-256MB" if s < 256 * MB else ">=256MB"
        buckets[key] += s
    out["by_allocation_size_mb"] = {k: _mb(v) for k, v in buckets.items()}

    class HeapSummary(ctypes.Structure):
        _fields_ = [("cb", wintypes.DWORD), ("cbAllocated", ctypes.c_size_t), ("cbCommitted", ctypes.c_size_t),
                    ("cbReserved", ctypes.c_size_t), ("cbMaxReserve", ctypes.c_size_t)]

    try:
        k32.GetProcessHeaps.argtypes = [wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE)]
        n = k32.GetProcessHeaps(0, None)
        heaps = (wintypes.HANDLE * max(n, 1))()
        n = k32.GetProcessHeaps(n, heaps)
        k32.HeapSummary.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(HeapSummary)]
        rows = []
        for h in heaps[:n]:
            s = HeapSummary()
            s.cb = ctypes.sizeof(s)
            if k32.HeapSummary(h, 0, ctypes.byref(s)):
                rows.append((s.cbCommitted, s.cbAllocated))
        rows.sort(reverse=True)
        out["heaps"] = len(rows)
        out["heaps_committed_mb"] = _mb(sum(c for c, _ in rows))
        out["heaps_allocated_mb"] = _mb(sum(a for _, a in rows))
        # Committed but unused: freed after a past peak and kept by the heap (heap_trim gives it back).
        out["heap_free_mb"] = _mb(sum(max(c - a, 0) for c, a in rows))
        out["biggest_heaps_mb"] = [{"committed": _mb(c), "allocated": _mb(a)} for c, a in rows[:5]]
    except Exception as exc:  # noqa: BLE001
        out["heaps_error"] = str(exc)
    if "clr" in sys.modules or "pythonnet" in sys.modules:
        try:
            from System import GC  # type: ignore[import-not-found]

            info = GC.GetGCMemoryInfo()
            out["dotnet"] = {"total_memory_mb": _mb(GC.GetTotalMemory(False)),
                             "heap_size_mb": _mb(info.HeapSizeBytes), "committed_mb": _mb(info.TotalCommittedBytes),
                             "fragmented_mb": _mb(info.FragmentedBytes), "gen2_collections": GC.CollectionCount(2)}
        except Exception as exc:  # noqa: BLE001
            out["dotnet_error"] = str(exc)[:200]
    return out


def _gc(collect: bool, top: int) -> dict[str, Any]:
    out: dict[str, Any] = {}
    if collect:
        t = time.monotonic()
        out["collected"] = gc.collect()
        out["collect_s"] = round(time.monotonic() - t, 2)
    out["garbage"] = len(gc.garbage)
    objects = gc.get_objects()
    out["tracked"] = len(objects)
    # str(): some extension types carry a non-str __module__ (None, a descriptor).
    counts = collections.Counter(
        f"{getattr(type(o), '__module__', None)}.{getattr(type(o), '__qualname__', type(o).__name__)}"
        for o in objects
    )
    del objects
    out["types"] = [{"type": k, "count": n} for k, n in counts.most_common(top)]
    return out


def _walk(root: Any, deadline: float) -> tuple[int, int, bool]:
    """``(bytes, objects, partial)`` of what ``root`` reaches."""
    seen: set[int] = set()
    stack = [root]
    size = count = 0
    while stack:
        obj = stack.pop()
        key = id(obj)
        if key in seen:
            continue
        seen.add(key)
        if isinstance(obj, _SKIP):
            continue
        try:
            size += sys.getsizeof(obj)
        except Exception:  # noqa: BLE001 — a proxy that won't say
            pass
        count += 1
        if count >= ROOT_MAX_OBJECTS or (not count & 4095 and time.monotonic() > deadline):
            return size, count, True
        if isinstance(obj, _ATOMS):
            continue
        try:
            stack.extend(gc.get_referents(obj))
        except Exception:  # noqa: BLE001
            pass
    return size, count, False


def _roots() -> list[tuple[str, Any]]:
    """Module globals outside the standard library, then each thread's frame locals."""
    stdlib = getattr(sys, "stdlib_module_names", frozenset())
    out: list[tuple[str, Any]] = []
    for name, mod in list(sys.modules.items()):
        if mod is None or name.split(".")[0] in stdlib or name == __name__:
            continue
        try:
            items = list(vars(mod).items())
        except TypeError:
            continue
        for attr, value in items:
            if not attr.startswith("__") and not isinstance(value, _SKIP + _SMALL):
                out.append((f"{name}.{attr}", value))
    names = {t.ident: t.name for t in threading.enumerate()}
    me = threading.get_ident()
    for ident, frame in sys._current_frames().items():
        if ident == me:
            continue
        depth = 0
        while frame is not None and depth < 40:
            try:
                local = list(frame.f_locals.items())
            except Exception:  # noqa: BLE001
                local = []
            where = f"thread {names.get(ident, ident)}: {frame.f_code.co_name}"
            out.extend((f"{where}.{k}", v) for k, v in local if not isinstance(v, _SKIP + _SMALL))
            frame, depth = frame.f_back, depth + 1
    return out


def _children(value: Any) -> list[tuple[str, Any]]:
    """One level down: a dict's entries, or an object's attributes."""
    if isinstance(value, dict):
        return [(repr(k)[:80], v) for k, v in list(value.items())]
    d = getattr(value, "__dict__", None)
    return [(k, v) for k, v in list(d.items())] if isinstance(d, dict) else []


def _containers(top: int, budget_s: float) -> dict[str, Any]:
    deadline = time.monotonic() + budget_s
    rows: list[dict[str, Any]] = []
    values: dict[str, Any] = {}
    done: set[int] = set()
    roots = _roots()
    skipped = 0
    for name, value in roots:
        if id(value) in done:
            continue
        if time.monotonic() > deadline:
            skipped += 1
            continue
        done.add(id(value))
        size, count, partial = _walk(value, min(deadline, time.monotonic() + budget_s / 4))
        if size < 64 * 1024:
            continue
        try:
            length = len(value)
        except Exception:  # noqa: BLE001
            length = None
        rows.append({"name": name, "type": type(value).__qualname__, "len": length, "mb": _mb(size),
                     "objects": count, "partial": partial})
        values[name] = value
    rows.sort(key=lambda r: -r["mb"])
    # The biggest ones, one level down: which entry or attribute holds it.
    inner = time.monotonic() + budget_s / 2
    for row in rows[:8]:
        parts = []
        for key, child in _children(values[row["name"]])[:2000]:
            if time.monotonic() > inner:
                break
            size, count, partial = _walk(child, min(inner, time.monotonic() + budget_s / 8))
            parts.append({"key": key, "type": type(child).__qualname__, "mb": _mb(size), "partial": partial})
        parts.sort(key=lambda r: -r["mb"])
        if parts:
            row["biggest"] = parts[:6]
    return {"roots": len(roots), "skipped_for_time": skipped, "top": rows[:top],
            "note": "approximate deep sizes; an object reached from two roots counts in both"}


def _site(stat: Any) -> str:
    frame = stat.traceback[0]
    parts = frame.filename.replace("\\", "/").split("/")
    return f"{'/'.join(parts[-3:])}:{frame.lineno}"


def _trace(start: bool, stop: bool, top: int) -> dict[str, Any] | None:
    with _lock:
        if stop:
            tracemalloc.stop()
            _snapshot.clear()
            return {"tracing": False}
        if start and not tracemalloc.is_tracing():
            tracemalloc.start(1)
            _snapshot.clear()
        if not tracemalloc.is_tracing():
            return None
        snap = tracemalloc.take_snapshot().filter_traces((tracemalloc.Filter(False, tracemalloc.__file__),))
        current, peak = tracemalloc.get_traced_memory()
        out: dict[str, Any] = {
            "tracing": True, "traced_mb": _mb(current), "traced_peak_mb": _mb(peak),
            "top": [{"site": _site(s), "mb": _mb(s.size), "count": s.count} for s in snap.statistics("lineno")[:top]],
        }
        if _snapshot:
            out["grew"] = [{"site": _site(s), "mb": _mb(s.size_diff), "count": s.count_diff, "now_mb": _mb(s.size)}
                           for s in snap.compare_to(_snapshot[0], "lineno")[:top]]
        _snapshot[:] = [snap]
        return out


def report(*, start_trace: bool = False, stop_trace: bool = False, collect: bool = True,
           top: int = TOP, walk_budget_s: float = WALK_BUDGET_S) -> dict[str, Any]:
    started = time.monotonic()
    top = max(1, min(int(top or TOP), 200))
    out: dict[str, Any] = {"process": _process(), "native": _native(), "gc": _gc(collect, top),
                           "containers": _containers(top, max(1.0, float(walk_budget_s)))}
    traced = _trace(bool(start_trace), bool(stop_trace), min(top, TRACE_TOP))
    if traced is not None:
        out["tracemalloc"] = traced
    out["took_s"] = round(time.monotonic() - started, 2)
    return out
