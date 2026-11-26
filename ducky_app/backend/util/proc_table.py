"""Every process on the PC in one call: pid, parent, name, memory, threads, handles, CPU time.

On Windows one NtQuerySystemInformation call returns them all, so a look at the whole PC costs
milliseconds even when it is out of memory. Linux reads ``/proc`` directly (``_linux_table``). psutil asks each process on its own (an OpenProcess
per process per field), and on a busy PC that took 50-120 ms a process: minutes for one look
at ~700 processes, exactly when someone asks why the PC is slow.
"""

from __future__ import annotations

import os
import sys
import time
from typing import NamedTuple

_EPOCH_DIFF_S = 11_644_473_600  # 1601-01-01 to 1970-01-01


class Proc(NamedTuple):
    pid: int
    ppid: int
    name: str
    rss: int  # working set, bytes
    private: int  # private (commit) bytes
    threads: int
    handles: int
    cpu_s: float  # user + kernel seconds so far
    started: float  # epoch seconds (0 when unknown)


def table() -> dict[int, Proc]:
    if sys.platform == "win32":
        try:
            return _nt_table()
        except OSError:
            pass
    elif sys.platform.startswith("linux") and os.path.isdir("/proc/self"):
        return _linux_table()
    elif sys.platform == "darwin":
        try:
            return _mac_table()
        except Exception:  # noqa: BLE001 — no ps: psutil's per-process walk
            pass
    return _psutil_table()


def sample(interval: float = 1.0) -> tuple[dict[int, Proc], dict[int, float]]:
    """The table now, and each process's CPU % over ``interval`` (100 = one full core)."""
    first = table()
    start = time.monotonic()
    time.sleep(max(0.05, interval))
    last = table()
    span = max(1e-3, time.monotonic() - start)
    cpu = {}
    for pid, proc in last.items():
        old = first.get(pid)
        if old is not None and old.started == proc.started:
            cpu[pid] = round(max(0.0, proc.cpu_s - old.cpu_s) / span * 100, 1)
    return last, cpu


def tree(procs: dict[int, Proc], root: int) -> list[int]:
    """``root`` and everything it started, from one table (a reused parent pid never counts)."""
    kids: dict[int, list[int]] = {}
    for proc in procs.values():
        if proc.pid != proc.ppid:
            kids.setdefault(proc.ppid, []).append(proc.pid)
    out: list[int] = []
    stack = [root] if root in procs else []
    seen: set[int] = set()
    while stack:
        pid = stack.pop()
        if pid in seen:
            continue
        seen.add(pid)
        out.append(pid)
        parent = procs[pid]
        stack.extend(k for k in kids.get(pid, ()) if procs[k].started >= parent.started)
    return out


def ancestor(procs: dict[int, Proc], pid: int, name: str) -> Proc | None:
    """The nearest process above ``pid`` called ``name`` (a reused parent pid never counts).

    A backend started by a version hand-off has the stub ``UEFN-Ducky.exe`` as its parent and
    ``Ducky-Launcher.exe`` above that, so the launcher is not always ``os.getppid()``."""
    proc = procs.get(pid)
    for _ in range(8):
        parent = procs.get(proc.ppid) if proc is not None and proc.ppid != proc.pid else None
        if parent is None or (parent.started and proc.started and parent.started > proc.started):
            return None
        if parent.name.lower() == name.lower():
            return parent
        proc = parent
    return None


def orphan(procs: dict[int, Proc], pid: int) -> bool:
    """True when ``pid``'s parent is gone (or its pid now belongs to a newer process)."""
    proc = procs.get(pid)
    if proc is None:
        return False
    parent = procs.get(proc.ppid)
    return parent is None or (parent.started and proc.started and parent.started > proc.started)


def group(pgid: int, proc_root: str = "/proc") -> list[int]:
    """Every live process in POSIX process group ``pgid`` (Linux /proc; [] elsewhere).

    A group keeps a program's whole tree, even children whose parent died and were
    re-parented to init, which a ppid walk (``tree``) loses."""
    out: list[int] = []
    try:
        names = os.listdir(proc_root)
    except OSError:
        return out
    for name in names:
        if not name.isdigit():
            continue
        try:
            with open(f"{proc_root}/{name}/stat", "rb") as fh:
                stat = fh.read()
        except OSError:
            continue
        # "pid (comm) state ppid pgrp ...": comm may hold spaces and parens, so split after the last ")".
        fields = stat[stat.rfind(b")") + 2:].split()
        if len(fields) > 2 and fields[0] != b"Z" and int(fields[2]) == int(pgid):
            out.append(int(name))
    return out


def _linux_table(proc_root: str = "/proc") -> dict[int, Proc]:
    """Linux: every process from ``/proc/<pid>/stat`` and ``statm`` (two small reads each, no psutil objects)."""
    tick = os.sysconf("SC_CLK_TCK") or 100
    page = os.sysconf("SC_PAGE_SIZE") or 4096
    boot = 0.0
    try:
        with open(f"{proc_root}/stat", "rb") as fh:
            for line in fh:
                if line.startswith(b"btime "):
                    boot = float(line.split()[1])
                    break
    except OSError:
        pass
    out: dict[int, Proc] = {}
    for entry in os.listdir(proc_root):
        if not entry.isdigit():
            continue
        base = f"{proc_root}/{entry}"
        try:
            with open(f"{base}/stat", "rb") as fh:
                stat = fh.read()
            with open(f"{base}/statm", "rb") as fh:
                statm = fh.read().split()
        except OSError:
            continue  # it exited
        # "pid (comm) state ppid ...": comm may hold spaces and parens, so split after the last ")".
        close = stat.rfind(b")")
        name = stat[stat.find(b"(") + 1:close].decode("utf-8", "replace")
        f = stat[close + 2:].split()
        if len(f) < 22 or len(statm) < 3:
            continue
        if len(name) == 15:  # comm is cut at 15 chars: the real name is argv[0]'s file name
            try:
                with open(f"{base}/cmdline", "rb") as fh:
                    argv0 = os.path.basename(fh.read().split(b"\0", 1)[0].decode("utf-8", "replace"))
                if argv0.startswith(name):
                    name = argv0
            except OSError:
                pass
        resident, shared = int(statm[1]) * page, int(statm[2]) * page
        out[int(entry)] = Proc(
            int(entry), int(f[1]), name, resident, max(0, resident - shared), int(f[17]), 0,
            (int(f[11]) + int(f[12])) / tick, boot + int(f[19]) / tick if boot else 0.0,
        )
    return out


def _clock_s(text: str) -> float:
    """ps's ``[[dd-]hh:]mm:ss[.cc]`` as seconds."""
    days, _, rest = text.rpartition("-")
    total = 0.0
    for part in rest.split(":"):
        total = total * 60 + float(part)
    return total + (int(days) * 86400 if days else 0)


def _mac_table(text: str | None = None) -> dict[int, Proc]:
    """macOS: every process from one ``ps`` call (psutil asks each process on its own).
    No private bytes on macOS: rss. Threads only for this process (ps has no count)."""
    if text is None:
        import subprocess

        text = subprocess.run(["ps", "-axo", "pid=,ppid=,rss=,time=,lstart=,comm="], capture_output=True,
                              text=True, encoding="utf-8", errors="replace", timeout=10, check=True,
                              env={**os.environ, "LC_ALL": "C"}).stdout
    out: dict[int, Proc] = {}
    for line in text.splitlines():
        f = line.split(None, 9)  # lstart is 5 words ("Thu Nov 26 22:36:26 2026"); comm may hold spaces
        if len(f) < 10 or not f[0].isdigit():
            continue
        try:
            started = time.mktime(time.strptime(" ".join(f[4:9]), "%a %b %d %H:%M:%S %Y"))
        except ValueError:
            started = 0.0
        rss = int(f[2]) * 1024
        out[int(f[0])] = Proc(int(f[0]), int(f[1]), os.path.basename(f[9].strip()), rss, rss, 0, 0,
                              _clock_s(f[3]), started)
    me = out.get(os.getpid())
    if me is not None:
        try:
            import psutil

            out[me.pid] = me._replace(threads=psutil.Process().num_threads())
        except Exception:  # noqa: BLE001
            pass
    return out


def mac_thread_times() -> dict[int, float]:
    """macOS: CPU seconds of each thread of this process, keyed like ``threading.get_native_id()``
    (psutil's macOS thread ids are 1-based indexes no Thread matches). Mach task_threads + thread_info."""
    import ctypes
    from ctypes import POINTER, byref, c_int, c_size_t, c_uint, c_uint64, c_void_p

    lib = ctypes.CDLL("/usr/lib/libSystem.B.dylib")
    lib.task_threads.argtypes = [c_uint, POINTER(POINTER(c_uint)), POINTER(c_uint)]
    lib.thread_info.argtypes = [c_uint, c_int, c_void_p, POINTER(c_uint)]
    lib.mach_port_deallocate.argtypes = [c_uint, c_uint]
    lib.vm_deallocate.argtypes = [c_uint, c_size_t, c_size_t]
    task = c_uint.in_dll(lib, "mach_task_self_").value
    threads, count = POINTER(c_uint)(), c_uint()
    if lib.task_threads(task, byref(threads), byref(count)) != 0:
        return {}
    out: dict[int, float] = {}
    try:
        for i in range(count.value):
            port = threads[i]
            basic, n = (c_int * 10)(), c_uint(10)  # THREAD_BASIC_INFO: user and system time_value_t first
            ident, m = (c_uint64 * 3)(), c_uint(6)  # THREAD_IDENTIFIER_INFO: thread_id first
            if lib.thread_info(port, 3, basic, byref(n)) == 0 and lib.thread_info(port, 4, ident, byref(m)) == 0:
                out[int(ident[0])] = basic[0] + basic[1] / 1e6 + basic[2] + basic[3] / 1e6
            lib.mach_port_deallocate(task, port)
    finally:
        lib.vm_deallocate(task, ctypes.cast(threads, c_void_p).value or 0, count.value * ctypes.sizeof(c_uint))
    return out


def _psutil_table() -> dict[int, Proc]:
    import psutil

    out: dict[int, Proc] = {}
    for p in psutil.process_iter(["ppid", "name", "memory_info", "num_threads", "cpu_times", "create_time"]):
        info = p.info
        mem = info.get("memory_info")
        times = info.get("cpu_times")
        rss = int(getattr(mem, "rss", 0) or 0)
        # Linux has no private bytes; rss - shared is the closest. macOS has neither: rss. Never vms
        # (GBs of reservations on Linux, hundreds of GB per process on macOS).
        private = getattr(mem, "private", 0) or (rss - int(mem.shared) if hasattr(mem, "shared") else 0)
        out[p.pid] = Proc(
            p.pid, int(info.get("ppid") or 0), str(info.get("name") or ""),
            rss, int(private or rss),
            int(info.get("num_threads") or 0), 0,
            float(getattr(times, "user", 0.0) or 0.0) + float(getattr(times, "system", 0.0) or 0.0),
            float(info.get("create_time") or 0.0),
        )
    return out


def _nt_table() -> dict[int, Proc]:
    import ctypes
    from ctypes import c_longlong, c_size_t, c_ulong, c_ulonglong, c_ushort, c_void_p

    class _UnicodeString(ctypes.Structure):
        _fields_ = [("Length", c_ushort), ("MaximumLength", c_ushort), ("Buffer", c_void_p)]

    class _ProcInfo(ctypes.Structure):  # SYSTEM_PROCESS_INFORMATION
        _fields_ = [
            ("NextEntryOffset", c_ulong), ("NumberOfThreads", c_ulong),
            ("WorkingSetPrivateSize", c_longlong), ("HardFaultCount", c_ulong),
            ("NumberOfThreadsHighWatermark", c_ulong), ("CycleTime", c_ulonglong),
            ("CreateTime", c_longlong), ("UserTime", c_longlong), ("KernelTime", c_longlong),
            ("ImageName", _UnicodeString), ("BasePriority", ctypes.c_long),
            ("UniqueProcessId", c_void_p), ("InheritedFromUniqueProcessId", c_void_p),
            ("HandleCount", c_ulong), ("SessionId", c_ulong), ("UniqueProcessKey", c_void_p),
            ("PeakVirtualSize", c_size_t), ("VirtualSize", c_size_t), ("PageFaultCount", c_ulong),
            ("PeakWorkingSetSize", c_size_t), ("WorkingSetSize", c_size_t),
            ("QuotaPeakPagedPoolUsage", c_size_t), ("QuotaPagedPoolUsage", c_size_t),
            ("QuotaPeakNonPagedPoolUsage", c_size_t), ("QuotaNonPagedPoolUsage", c_size_t),
            ("PagefileUsage", c_size_t), ("PeakPagefileUsage", c_size_t), ("PrivatePageCount", c_size_t),
        ]

    query = ctypes.WinDLL("ntdll").NtQuerySystemInformation
    query.argtypes = [c_ulong, c_void_p, c_ulong, ctypes.POINTER(c_ulong)]
    query.restype = ctypes.c_long
    size = 1 << 20
    for _ in range(6):
        buf = ctypes.create_string_buffer(size)
        needed = c_ulong(0)
        status = query(5, buf, size, ctypes.byref(needed))  # SystemProcessInformation
        if status == 0:
            break
        if status & 0xFFFFFFFF != 0xC0000004:  # anything but STATUS_INFO_LENGTH_MISMATCH
            raise OSError(f"NtQuerySystemInformation failed: {status & 0xFFFFFFFF:#x}")
        size = max(size * 2, needed.value + (64 << 10))
    else:
        raise OSError("NtQuerySystemInformation kept growing")
    out: dict[int, Proc] = {}
    offset = 0
    while True:
        info = _ProcInfo.from_buffer(buf, offset)
        pid = int(info.UniqueProcessId or 0)
        name = ctypes.wstring_at(info.ImageName.Buffer, info.ImageName.Length // 2) if info.ImageName.Buffer else ""
        created = info.CreateTime / 1e7 - _EPOCH_DIFF_S if info.CreateTime > 0 else 0.0
        out[pid] = Proc(
            pid, int(info.InheritedFromUniqueProcessId or 0), name or ("System Idle Process" if pid == 0 else ""),
            int(info.WorkingSetSize), int(info.PagefileUsage), int(info.NumberOfThreads), int(info.HandleCount),
            (info.UserTime + info.KernelTime) / 1e7, created,
        )
        if not info.NextEntryOffset:
            break
        offset += info.NextEntryOffset
    return out


def own() -> Proc | None:
    return table().get(os.getpid())
