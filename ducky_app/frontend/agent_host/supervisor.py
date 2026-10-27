"""App side: start agent-host child processes on demand and keep them in Windows job objects.

One host per ``PER_HOST`` running agents, at most ``MAX_HOSTS``; each has its own GIL, so 16
agents run on 4 cores instead of one. Every host sits in a job with KILL_ON_JOB_CLOSE (the
sandbox's ``jobs.Launched``), so when the app process ends, for any reason, its hosts end too.
A host that has had no agents for ``IDLE_EXIT_S`` is told to shut down and then killed.

``assign(conv_id)`` picks (or starts) the host for a turn; ``release(conv_id)`` frees the seat.
"""

from __future__ import annotations

import os
import sys
import tempfile
import threading
import time
from multiprocessing.connection import Client
from pathlib import Path
from typing import Any, Callable

from backend.sandbox import jobs
from frontend.agent_host import protocol

KEY_ENV = "UEFN_DUCKY_HOST_KEY"
PER_HOST = 4
MAX_HOSTS = 8
IDLE_EXIT_S = 300.0
CONNECT_S = 60.0
_REAP_S = 5.0
_DUCKY_APP = str(Path(__file__).resolve().parents[2])
# ponytail: visible_chats is added here, not in protocol.APP_OPS (another lane); fold it in there later.
APP_OPS = protocol.APP_OPS | {"visible_chats"}

OnFrame = Callable[["Host", dict[str, Any]], None]


def host_argv(slot: int, pipe: str) -> list[str]:
    """``UEFN-Ducky.exe agent-host ...`` frozen, ``python -m frontend agent-host ...`` from source."""
    mode = ["agent-host", "--slot", str(slot), "--pipe", pipe]
    return [sys.executable, *mode] if getattr(sys, "frozen", False) else [sys.executable, "-m", "frontend", *mode]


class Host:
    """One running host process and its pipe."""

    def __init__(self, slot: int, pipe: str, key: bytes, launched: jobs.Launched) -> None:
        self.slot = slot
        self.pipe = pipe
        self.key = key
        self.launched = launched
        self.channel: protocol.Channel | None = None
        self.ready = threading.Event()
        self.agents: set[str] = set()
        self.started = self.idle_since = time.monotonic()
        self.stopping = False
        self.dead = False

    @property
    def pid(self) -> int:
        return self.launched.pid

    def alive(self) -> bool:
        return self.launched.alive() and not (self.channel is not None and self.channel.closed)

    def send(self, op: str, **fields: Any) -> bool:
        """False until the host connected, or once it is gone."""
        return bool(self.channel) and self.channel.send(op, **fields)  # type: ignore[union-attr]

    def stop(self, grace_s: float = 2.0) -> None:
        """Ask the host to exit, then end its job (the host and every CLI it started)."""
        self.stopping = True
        if self.channel is not None and self.channel.send("shutdown"):
            try:
                self.launched.proc.wait(timeout=grace_s)
            except Exception:  # noqa: BLE001 - TimeoutExpired: the job kill below ends it
                pass
        elif not self.ready.is_set():
            _unblock_accept(self.pipe, self.key)
        if self.channel is not None:
            self.channel.close("stopped")
        self.launched.stop(timeout_s=5.0)


def _unblock_accept(pipe: str, key: bytes) -> None:
    """A host that died before connecting leaves ``listener.accept`` waiting; connect once to free it."""
    try:
        Client(pipe, authkey=key).close()
    except Exception:  # noqa: BLE001 - the listener may already be closed
        pass


class Supervisor:
    def __init__(
        self,
        *,
        on_frame: OnFrame | None = None,
        on_host_closed: Callable[[Host, str], None] | None = None,
        argv_for: Callable[[int, str], list[str]] = host_argv,
        log_dir: Path | None = None,
        per_host: int = PER_HOST,
        max_hosts: int = MAX_HOSTS,
        idle_exit_s: float = IDLE_EXIT_S,
        reap_s: float = _REAP_S,
    ) -> None:
        self._on_frame = on_frame
        self._on_host_closed = on_host_closed
        self._argv_for = argv_for
        base = Path(os.environ.get("LOCALAPPDATA") or tempfile.gettempdir())
        self._log_dir = log_dir or base / "UEFN-Ducky" / "logs" / "agent_hosts"
        self.per_host = per_host
        self.max_hosts = max_hosts
        self.idle_exit_s = idle_exit_s
        self._reap_s = reap_s
        self._lock = threading.Lock()
        self._hosts: dict[int, Host] = {}
        self._by_conv: dict[str, Host] = {}
        self._visible: set[str] | None = None  # None: never told, every chat gets live text
        self._stopped = threading.Event()
        threading.Thread(target=self._reap_loop, name="agent-host-reaper", daemon=True).start()

    # --- seats -------------------------------------------------------------------------------
    def assign(self, conv_id: str) -> Host:
        """The host that runs this chat's turns: its current one, the least-busy host with a free
        seat, a new host, or (all ``max_hosts`` full) the least-busy host."""
        with self._lock:
            host = self._by_conv.get(conv_id)
            if host is not None and host.alive():
                return host
            live = [h for h in self._hosts.values() if h.alive()]
            free = [h for h in live if len(h.agents) < self.per_host]
            if free:
                host = min(free, key=lambda h: len(h.agents))
            elif len(live) < self.max_hosts:
                host = self._start(self._free_slot())
            else:
                host = min(live, key=lambda h: len(h.agents))
            host.agents.add(conv_id)
            self._by_conv[conv_id] = host
        self._send_visible(host)
        return host

    def release(self, conv_id: str) -> None:
        with self._lock:
            host = self._by_conv.pop(conv_id, None)
            if host is not None:
                host.agents.discard(conv_id)
                if not host.agents:
                    host.idle_since = time.monotonic()

    def set_visible(self, conv_ids: list[str] | None) -> None:
        """The chats on screen (panel_api.set_visible_chats). Each host hears which of its chats
        those are; the window loads a newly shown chat's full text itself (load_messages)."""
        with self._lock:
            self._visible = None if conv_ids is None else {str(c) for c in conv_ids}
            hosts = list(self._hosts.values())
        for host in hosts:
            self._send_visible(host)

    def _send_visible(self, host: Host) -> None:
        with self._lock:
            if self._visible is None:
                return
            mine = sorted(self._visible & host.agents)
        host.send("visible_chats", conv_ids=mine)

    def host_for(self, conv_id: str) -> Host | None:
        with self._lock:
            return self._by_conv.get(conv_id)

    def hosts(self) -> list[Host]:
        with self._lock:
            return list(self._hosts.values())

    def snapshot(self) -> list[dict[str, Any]]:
        """For ducky_health: one row per host."""
        return [
            {"slot": h.slot, "pid": h.pid, "agents": len(h.agents), "connected": h.ready.is_set(), "alive": h.alive()}
            for h in self.hosts()
        ]

    def stop_all(self) -> None:
        self._stopped.set()
        with self._lock:
            hosts = list(self._hosts.values())
            self._hosts.clear()
            self._by_conv.clear()
        for host in hosts:
            host.stop()

    # --- processes ---------------------------------------------------------------------------
    def _free_slot(self) -> int:
        slot = 0
        while slot in self._hosts:
            slot += 1
        return slot

    def _start(self, slot: int) -> Host:
        key = os.urandom(32)
        tag = f"{slot}-{os.urandom(3).hex()}"  # a restarted slot never reuses a pipe name
        name = protocol.pipe_name(tag)
        listener = protocol.listen(tag, key)
        # The slot's agents share one Ducky tool server (backend.bridge.shared_mcp).
        env = {**os.environ, KEY_ENV: key.hex(), "UEFN_DUCKY_SHARED_MCP_SLOT": str(slot)}
        try:
            # ponytail: no job memory cap here; turn admission (p5-admit) keeps memory in check.
            launched = jobs.Launched(
                self._argv_for(slot, name), cwd=_DUCKY_APP, env=env,
                log=self._log_dir / f"host-{slot}.log", memory_mb=0,
            )
        except Exception:
            listener.close()
            raise
        host = Host(slot, name, key, launched)
        self._hosts[slot] = host
        threading.Thread(target=self._accept, args=(host, listener), name=f"agent-host-accept-{slot}",
                         daemon=True).start()
        return host

    def _accept(self, host: Host, listener: Any) -> None:
        try:
            conn = listener.accept()
        except Exception:  # noqa: BLE001 - the host died before connecting
            return
        finally:
            listener.close()
        host.channel = protocol.Channel(
            conn, send_ops=APP_OPS, recv_ops=protocol.HOST_OPS,
            on_frame=lambda frame: self._on_frame(host, frame) if self._on_frame else None,
            on_closed=lambda reason: self._closed(host, reason), dead_after_s=protocol.DEAD_AFTER_S,
        ).start()
        host.ready.set()
        self._send_visible(host)

    def _closed(self, host: Host, reason: str) -> None:
        if host.stopping:
            with self._lock:
                self._drop(host)
            return
        self._died(host, reason)

    def _drop(self, host: Host) -> list[str]:
        """Forget a host and its seats (call under the lock); returns the chats it had."""
        if self._hosts.get(host.slot) is host:
            del self._hosts[host.slot]
        convs = sorted(host.agents)
        for conv in convs:
            if self._by_conv.get(conv) is host:
                del self._by_conv[conv]
        return convs

    def _died(self, host: Host, reason: str) -> None:
        """A host crashed (pipe EOF, no heartbeat, process gone): only its own chats stop. Its
        turns are marked dead once, a fresh host starts warm, and those chats' next messages land
        there (least-busy pick), where the runner resumes each from its stored session id."""
        with self._lock:
            if host.dead:
                return
            host.dead = True
            convs = self._drop(host)
        threading.Thread(target=host.stop, name=f"agent-host-kill-{host.slot}", daemon=True).start()
        try:
            from frontend.agent_host import turns

            mark = getattr(turns, "mark_host_dead", None)
            if callable(mark):
                mark(host.slot)
        except Exception:  # noqa: BLE001 - a failing hook never stops the restart
            pass
        if self._on_host_closed:
            try:
                self._on_host_closed(host, reason)
            except Exception:  # noqa: BLE001
                pass
        if convs and not self._stopped.is_set():
            with self._lock:
                if len(self._hosts) < self.max_hosts:
                    try:
                        self._start(self._free_slot())
                    except Exception:  # noqa: BLE001 - the next assign tries again
                        pass

    def _reap_loop(self) -> None:
        while not self._stopped.wait(self._reap_s):
            self.reap()

    def reap(self) -> None:
        """Stop hosts idle past ``idle_exit_s``, hosts that never connected, and dead ones."""
        now = time.monotonic()
        doomed: list[tuple[Host, str]] = []
        with self._lock:
            for host in list(self._hosts.values()):
                if not host.launched.alive():
                    reason = "exited"
                elif not host.ready.is_set() and now - host.started >= CONNECT_S:
                    reason = "never connected"
                elif not host.agents and now - host.idle_since >= self.idle_exit_s:
                    reason = "idle"
                else:
                    continue
                if reason == "idle":
                    self._drop(host)  # under the same lock as the check: no seat can sneak in
                doomed.append((host, reason))
        for host, reason in doomed:
            if reason == "idle":
                host.stop()
            else:
                self._died(host, reason)


_SUPERVISOR: Supervisor | None = None


def get() -> Supervisor | None:
    """The app's supervisor, once something started it (``start``)."""
    return _SUPERVISOR


def start(**kwargs: Any) -> Supervisor:
    global _SUPERVISOR
    if _SUPERVISOR is None:
        _SUPERVISOR = Supervisor(**kwargs)
    return _SUPERVISOR
