"""Thread-safe terminal session manager with agent command approval."""

from __future__ import annotations

import functools
import hashlib
import json
import logging
import os
import threading
import time
import uuid
import weakref
from typing import Any, Callable

from frontend.settings import PanelSettings
from frontend.ui_web.lag.output_pacer import OutputPacer
from frontend.ui_web.terminal import commit_guard, resource_guard, run_durations
from frontend.ui_web.terminal.bridge import TerminalBridge
from frontend.ui_web.terminal.session import (
    _OUTPUT_RING_CHARS,
    PendingCommand,
    ProcessSnapshot,
    TerminalSession,
    _process_snapshot,
    agent_command_output,
    console_hosts,
    live_console_hosts,
    terminate_pid,
)
from frontend.ui_web.terminal.shells import agent_shell, shell_label

_keep_log = logging.getLogger("terminal.keep")
_APPROVAL_TIMEOUT_S = 120.0
# session.run_command's errors -> run_agent_command's "fail" kind (anything else: "error").
_RUN_FAILS = {"command timed out": "timeout", "session busy": "busy"}
# Terminals one chat may keep open. Each holds a shell, a conhost and a socket bridge,
# and an agent that opens one per turn in different folders piled them up all day.
_MAX_CHAT_SHELLS = 6
# An agent's terminal with no output, no typing and nothing running for this long, while its
# agent is not working, is closed. Agents never closed theirs: a team left one shell (a
# shell, a conhost and a socket bridge) per member open until Ducky quit (Oct 15 2026).
# Oct 22 2026: ten minutes still left a header full of finished duckies' shells; a ducky's
# terminal now closes about a minute after it finishes.
_AGENT_IDLE_CLOSE_S = 60.0
# Nov 6 2026: once its agent is done (chat not running, or a run let go) the wait is this short.
# Turn end closes a chat's terminals itself (close_for_turn_end); this catches a missed one (a
# killed host, a workflow node with no turn) within a reaper tick or two.
_DONE_IDLE_CLOSE_S = 10.0
# Oct 24 2026: a lead that stays running, or a hung child (git, a pager), kept shells open all
# day. Quiet this long with no agent command waiting, an agent's terminal closes anyway.
_AGENT_STALE_CLOSE_S = 600.0
# Nov 2026: a finished ducky's terminal stayed open for good when its last command still counted
# as busy (hung, or queued behind another chat's test run) or a background command it started
# still ran. Its agent done and the terminal quiet this long, it closes anyway, tree and all.
_ORPHAN_BACKGROUND_CLOSE_S = 3600.0
_REAP_EVERY_S = 5.0
# How often agent terminals' and sandbox programs' memory caps follow the shared budget.
_BUDGET_EVERY_S = 5.0
# A waited command (or its Allow card) stops waiting when the person writes: checked after
# the first 10 s, then every 2 s. The command keeps running (Oct 24 2026: 16 runs over 120 s
# in 3 h held the person's message until each ended).
_STEER_FIRST_S = 10.0
_STEER_EVERY_S = 2.0
_SO_FAR_CHARS = 4096
# terminal_busy pushes per session: at most one per half second (the last value always goes).
_BUSY_PUSH_GAP_S = 0.5
_STEER_NOTE = ("The person sent a message. Read it in this result, then check the command later "
               "with ducky_terminal_read_output.")
_STEER_NOTE_APPROVAL = ("The person sent a message. Read it in this result. The command runs once its "
                        "Allow/Deny card is answered; check it later with ducky_terminal_read_output.")
# A run the agent said takes expect_minutes comes back still running once it has taken three
# times that (never before this): Oct 31 2026 a test expected to take a minute held its agent
# for its whole ten-minute timeout.
_OVERDUE_MIN_S = 120.0
_OVERDUE_NOTE = ("It is taking much longer than the {minutes:g} min you expected, so it came back while it "
                 "still runs. Read its output with ducky_terminal_read_output; if it is stuck, close the "
                 "terminal with ducky_terminal_close.")
_manager: "TerminalManager | None" = None
_manager_lock = threading.Lock()
# Every manager in this process. A console host one of them owns is never another's stray:
# Nov 6 2026, a second manager's reaper ended a live terminal's conhost (its baseline and
# sessions knew nothing of it), the shell died mid-command and reads said "session not found".
_managers: "weakref.WeakSet[TerminalManager]" = weakref.WeakSet()


def _default_cwd() -> str:
    root = PanelSettings.load().uefn_project_root.strip()
    return root or "."


def _chat_title(conv_id: str) -> str:
    """The ducky (chat) a terminal belongs to, as the header list names it."""
    if not conv_id:
        return ""
    try:
        from frontend.ui_web.chats.conversations import conversation_title

        return conversation_title(conv_id)
    except Exception:
        return ""


class TerminalManager:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._sessions: dict[str, _SessionEntry] = {}
        self._pending: dict[str, PendingCommand] = {}
        # Answered before the agent's thread got to wait_for_approval (a quick click):
        # the waiter still finds the answer. The newest few only; a waiter pops its own.
        self._decided: dict[str, PendingCommand] = {}
        self._reaper: threading.Thread | None = None
        self._reaper_stop = threading.Event()
        self._budget_ticker: threading.Thread | None = None
        # Console hosts Ducky had before its first terminal (its own console): never reaped.
        self._host_baseline: set[int] | None = None
        _managers.add(self)
        # Terminals a running workflow still needs: never closed as idle until it lets go.
        self._held: set[str] = set()
        self._push: Callable[[dict[str, Any]], None] | None = None
        # session id -> {"value", "sent", "at", "timer"}: coalesced terminal_busy pushes.
        self._busy_push: dict[str, dict[str, Any]] = {}
        # Terminals each window shows (set_visible_terminals). Until one reports, every
        # terminal is treated as shown; the rest get one status frame a second.
        self._visible_terms: dict[str, set[str]] = {}
        # Why each terminal the last close_idle_agent_terminals pass left open stayed:
        # {session id: "quiet<60s" | "chat_running" | "busy" | "background" | "allow_card" |
        # "held" | "not_agent"}. Logged at debug (terminal.keep) at most once a minute.
        self.kept: dict[str, str] = {}
        self._kept_logged_at = float("-inf")

    def set_push(self, push: Callable[[dict[str, Any]], None] | None) -> None:
        self._push = push

    def _emit(self, event: dict[str, Any]) -> None:
        push = self._push
        if push:
            try:
                push(event)
            except Exception:
                pass

    def spawn(
        self,
        shell: str = "bash",
        cwd: str | None = None,
        title: str = "",
        *,
        push_open: bool = False,
        hidden: bool = False,
        conv_id: str = "",
        command: list[str] | None = None,
        env_extra: dict[str, str] | None = None,
        activate: bool = True,
        reuse_idle: bool = False,
        from_agent: bool = False,
    ) -> dict[str, Any]:
        """``reuse_idle``: hand back this chat's idle live terminal with the same shell
        and folder instead of a new one. Agents that open a terminal every turn used
        to stack a shell, a conhost and a socket bridge per call.

        ``from_agent``: an agent's tool call opened it. With no chat (``conv_id``) to own it,
        it still closes when idle, like a workflow run's; only the person's own stay."""
        shell_norm = shell_label(shell)
        if (conv_id or from_agent) and not command:
            shell_norm = agent_shell(shell)  # no Git Bash: light PowerShell, never an error
        swapped = shell_norm != shell_label(shell)
        workdir = (cwd or _default_cwd()).strip() or "."
        if not os.path.isdir(workdir):
            workdir = os.getcwd()
        argv = [str(a) for a in command] if command else None
        extra = {str(k): str(v) for k, v in (env_extra or {}).items() if k} or None
        # The environment is part of the key: a sandbox's shells (their own PATH, profile and
        # temp) are reused by its next command, never handed to a plain one, and a changed
        # PATH (a runtime just installed) gets a fresh shell.
        env_key = hashlib.sha1(json.dumps(sorted(extra.items())).encode("utf-8")).hexdigest() if extra else ""
        reuse_key = (conv_id, shell_norm, workdir, env_key) if conv_id and not argv and not hidden else None
        if reuse_idle and reuse_key is not None:
            idle = self._idle_session(reuse_key)
            if idle is not None:
                if push_open:
                    self._emit_open(idle, conv_id, activate)
                return {
                    "ok": True,
                    **idle.to_dict(),
                    "tab_id": f"terminal:{idle.id}",
                    "shell_fallback": False,
                    "reused": True,
                }
        closed = self._make_room(conv_id) if conv_id else []
        entry: _SessionEntry | None = None
        with self._lock:
            session = TerminalSession(
                shell=shell_norm,
                cwd=workdir,
                title=title,
                hidden=hidden,
                spawn_argv=argv,
                env_extra=extra,
                agent=bool(conv_id or from_agent),
            )  # type: ignore[arg-type]
            pacer = OutputPacer(
                lambda data: bridge.push_output(data),
                lambda status: bridge.push_status(status),
                replay=lambda s=session: s.read_output_tail(_OUTPUT_RING_CHARS),
            )
            bridge = TerminalBridge(
                on_input=lambda data, s=session: self._user_write(s.id, data),
                on_resize=lambda c, r, s=session: s.resize(c, r),
                # A tab shown again gets its whole kept history back, not just the last screen.
                get_replay=lambda s=session: s.read_output_tail(_OUTPUT_RING_CHARS),
                on_attach=pacer.drop_pending,
                get_status=lambda s=session: self._session_status(s),
            )
            bridge.start()
            session.port = bridge.port
            session.ws_url = bridge.ws_url
            pacer.set_visible(self._is_shown(session.id))

            def on_output(text: str, p=pacer) -> None:
                p.push(text)

            def on_exit(code: int, b=bridge, p=pacer) -> None:
                p.flush()
                b.push_exit(code)

            session._on_output = on_output  # type: ignore[attr-defined]
            session._on_exit = on_exit  # type: ignore[attr-defined]
            session._on_busy = lambda busy, sid=session.id: self._busy_changed(sid, busy)  # type: ignore[attr-defined]
            shell_fallback = swapped
            if self._host_baseline is None:
                self._host_baseline = console_hosts(_process_snapshot)
            try:
                session.spawn()
            except Exception as exc:
                if argv:
                    bridge.stop()
                    return {"ok": False, "error": str(exc)}
                if shell_norm == "bash":
                    session.shell = "powershell"
                    shell_norm = "powershell"
                    shell_fallback = True
                    try:
                        session.spawn()
                    except Exception as exc2:
                        bridge.stop()
                        return {"ok": False, "error": str(exc2), "hint": f"bash failed ({exc}); powershell also failed"}
                else:
                    bridge.stop()
                    return {"ok": False, "error": str(exc)}
            session.owner = _chat_title(conv_id)
            entry = _SessionEntry(session=session, bridge=bridge, reuse_key=reuse_key, chat=conv_id, pacer=pacer)
            entry.run_owned = bool(from_agent and not conv_id)
            self._sessions[session.id] = entry

        result = {
            "ok": True,
            **session.to_dict(),
            "tab_id": f"terminal:{session.id}",
            "shell_fallback": shell_fallback,
        }
        if closed:
            result["closed"] = closed
        if push_open and not session.hidden:
            self._emit_open(session, conv_id, activate)
        if conv_id or from_agent:
            self._ensure_reaper()
            self.ensure_budget_ticker()
        return result

    def _ensure_reaper(self) -> None:
        with self._lock:
            if self._reaper is not None and self._reaper.is_alive():
                return
            self._reaper = threading.Thread(target=self._reap_loop, daemon=True, name="terminal-reaper")
            self._reaper.start()

    def ensure_budget_ticker(self) -> None:
        """Every _BUDGET_EVERY_S, split the memory budget agent runs share with UEFN across the
        live agent terminals and sandbox programs (resource_guard.rebalance). Nothing waits."""
        if os.name != "nt":
            return
        with self._lock:
            if self._budget_ticker is not None and self._budget_ticker.is_alive():
                return
            self._budget_ticker = threading.Thread(target=self._budget_loop, daemon=True, name="memory-budget")
            self._budget_ticker.start()

    def _budget_loop(self) -> None:
        from backend.sandbox.jobs import shared_jobs

        while not self._reaper_stop.wait(_BUDGET_EVERY_S):
            try:
                if shared_jobs():
                    resource_guard.rebalance()
            except Exception:
                pass

    def _reap_loop(self) -> None:
        while not self._reaper_stop.wait(_REAP_EVERY_S):
            try:
                self.close_idle_agent_terminals()
            except Exception:
                pass
            try:
                self.reap_console_hosts()
            except Exception:
                pass

    def reap_console_hosts(self) -> list[int]:
        """End console hosts (conhost/OpenConsole) under Ducky that no open terminal owns: a
        terminal that closed without releasing its ConPTY left one behind for good. Hosts any
        manager in this process owns, or had before its first terminal, and any live session's
        (one with no manager too), are never stray; nothing is while a session is starting.

        ponytail: another manager's terminal mid-start (host up, session not yet listed) is
        not seen; only this manager's start is locked out. One manager per app makes it moot."""
        live = live_console_hosts()
        if live is None:
            return []
        others: set[int] = set(live)
        for mgr in list(_managers):
            if mgr is self:
                continue
            others |= set(mgr._host_baseline or ())
            for entry in list(mgr._sessions.values()):
                others |= set(getattr(entry.session, "console_hosts", ()) or ())
        with self._lock:  # a terminal starting now records its host before this looks
            if self._host_baseline is None:
                return []
            owned = set(others)
            for entry in self._sessions.values():
                owned |= set(getattr(entry.session, "console_hosts", ()) or ())
            stray = console_hosts(_process_snapshot) - owned - self._host_baseline
        for pid in stray:
            terminate_pid(pid)
        return sorted(stray)

    def hold(self, session_id: str) -> Callable[[], None]:
        """Keep this terminal from the idle reaper while a workflow run goes on; the run
        calls the returned function when it ends, and the terminal then closes when idle
        like an agent's (a run with no Agent step has no chat to own it)."""
        with self._lock:
            self._held.add(session_id)
            entry = self._sessions.get(session_id)
            if entry is not None:
                entry.run_owned = True
        self._ensure_reaper()

        return lambda: self.let_go(session_id)

    def let_go(self, session_id: str) -> None:
        """The run that held this terminal (:meth:`hold`) ended."""
        with self._lock:
            self._held.discard(session_id)

    def close_idle_agent_terminals(
        self,
        *,
        now: float | None = None,
        running: set[str] | None = None,
    ) -> list[str]:
        """Close agents' terminals they are done with: the agent is not working (or its chat
        is gone), nothing runs in it and it has been quiet for ``_AGENT_IDLE_CLOSE_S``. Quiet
        for ``_AGENT_STALE_CLOSE_S`` it closes even while its agent works or a leftover child
        (a hung git, a pager) sits in it. Always kept: one waiting on an Allow card, held by a
        running workflow, running an agent command, or whose background command (a dev
        server) still runs; a person's own terminals (no chat, not opened by an agent or a
        workflow run) are never closed. Why each open one stayed is left in :attr:`kept`."""
        now = time.time() if now is None else now
        if running is None:
            from frontend.ui_web.agent_turns.state import list_running_agents

            running = set(list_running_agents())
        with self._lock:
            agents = [(e.session, e.chat) for e in self._sessions.values() if e.chat or e.run_owned]
            kept = {sid: "not_agent" for sid, e in self._sessions.items() if not (e.chat or e.run_owned)}
            asked = {p.session_id for p in self._pending.values()}
            held = set(self._held)
        snapshot = functools.cache(_process_snapshot)
        closed: list[str] = []
        for session, chat in agents:
            quiet = now - float(getattr(session, "last_activity", now))
            done = not chat or chat not in running
            # A done agent's background job that ended closes with the same 10 s quiet wait,
            # counted from its last output: Nov 6 2026, closing it on the next tick lost that
            # output to the agent reading it (the loopback test read "session not found").
            background = session.background_alive(snapshot)
            if session.id in asked:
                kept[session.id] = "allow_card"
            elif session.id in held:
                kept[session.id] = "held"
            elif quiet < (_DONE_IDLE_CLOSE_S if done else _AGENT_IDLE_CLOSE_S):
                kept[session.id] = "quiet<60s"
            elif session.is_busy() and not (done and quiet >= _AGENT_STALE_CLOSE_S):
                kept[session.id] = "busy"
            elif background and not (done and quiet >= _ORPHAN_BACKGROUND_CLOSE_S):
                kept[session.id] = "background"
            elif quiet < _AGENT_STALE_CLOSE_S and chat in running:
                kept[session.id] = "chat_running"
            elif quiet < _AGENT_STALE_CLOSE_S and session.has_running_command(snapshot):
                kept[session.id] = "busy"
            elif self.kill(session.id).get("ok"):
                closed.append(session.id)
        self.kept = kept
        if kept and now - self._kept_logged_at >= 60.0:
            self._kept_logged_at = now
            _keep_log.debug("kept agent terminals: %s", kept)
        return closed

    def close_chat_terminals(self, chat_ids: list[str] | set[str], *, now: float | None = None,
                             force: bool = False) -> list[str]:
        """Close every terminal these chats opened (team stop, archive, plan done), stuck
        children and all (the shell's tree is killed). Kept: one waiting on an Allow card, one
        whose background command still runs, and one whose command printed in the last
        ``_AGENT_IDLE_CLOSE_S`` (the idle reaper closes it once it is quiet). Each close sends
        terminal_close. ``force`` (the chats were archived): only an Allow card keeps one."""
        ids = {str(c) for c in chat_ids or [] if c}
        if not ids:
            return []
        now = time.time() if now is None else now
        with self._lock:
            mine = [e.session for e in self._sessions.values() if e.chat in ids]
            asked = {p.session_id for p in self._pending.values()}
        snapshot = functools.cache(_process_snapshot)
        closed: list[str] = []
        for session in mine:
            if session.id in asked:
                continue
            if not force:
                if session.background_alive(snapshot):
                    continue
                recent = now - float(getattr(session, "last_activity", now)) < _AGENT_IDLE_CLOSE_S
                if recent and session.has_running_command(snapshot):
                    continue
            if self.kill(session.id).get("ok"):
                closed.append(session.id)
        return closed

    def close_for_turn_end(self, conv_id: str) -> list[str]:
        """The chat's turn ended: close its terminals now, hung children and all. Kept: one
        waiting on an Allow card, one a workflow run holds, one whose background command (a
        dev server) still runs; the idle reaper takes those later. Nov 5 2026 soak: terminals
        waited 60-75 s after every turn (600 s while the chat counted as running), 6 x ~90 MB.

        ponytail: chat-less agent terminals (a call with no identity) are left to the reaper;
        nothing ties them to this turn."""
        cid = (conv_id or "").strip()
        if not cid:
            return []
        with self._lock:
            mine = [e.session for e in self._sessions.values() if e.chat == cid]
            keep = {p.session_id for p in self._pending.values()} | set(self._held)
        snapshot = functools.cache(_process_snapshot)
        closed: list[str] = []
        for session in mine:
            if session.id in keep or session.background_alive(snapshot):
                continue
            if self.kill(session.id).get("ok"):
                closed.append(session.id)
        return closed

    def _idle_session(self, reuse_key: tuple[str, ...]) -> TerminalSession | None:
        with self._lock:
            candidates = [e.session for e in self._sessions.values() if e.reuse_key == reuse_key]
        for session in candidates:
            # A timed-out command can leave only a bash subshell running, which the busy check
            # takes for Git's shim and the shell: Oct 31 2026 the next sandbox_run got that
            # terminal and failed at once ("timed out and is still running").
            if session.is_alive() and not session.has_running_command() and not session.left_running():
                # Handed out again: in use, so the idle-terminal reaper must not close it now.
                session.last_activity = time.time()
                return session
        return None

    def _make_room(self, conv_id: str) -> list[str]:
        """Close the chat's oldest idle terminals so a new one keeps it within the cap.

        A terminal running a command or waiting on an Allow pop-up is never closed, and
        when every one is busy the new terminal opens anyway. A person's own terminals
        belong to no chat and are never counted. Nov 3 2026: a `cargo test` started with
        background=true runs in a `( … ) &` subshell the busy check doesn't see, and its
        terminal was closed under it ("session not found"): a live background job is busy."""
        with self._lock:
            mine = [e.session for e in self._sessions.values() if e.chat == conv_id]
            asked = {p.session_id for p in self._pending.values()}
        excess = len(mine) - _MAX_CHAT_SHELLS + 1
        if excess <= 0:
            return []
        snapshot = functools.cache(_process_snapshot)
        idle = [s for s in mine if s.id not in asked and not s.has_running_command(snapshot)
                and not s.background_alive(snapshot)]
        # Terminals whose shell already ended go first, then the oldest (open order).
        idle.sort(key=lambda s: s.is_alive())
        closed = [s.id for s in idle[:excess]]
        for session_id in closed:
            self.kill(session_id)
        return closed

    def _emit_open(self, session: TerminalSession, conv_id: str, activate: bool) -> None:
        self._emit(
            {
                "type": "terminal_open",
                "session_id": session.id,
                "shell": session.shell,
                "title": session.title,
                "cwd": session.cwd,
                "ws_url": session.ws_url,
                "conv_id": conv_id,
                "owner": session.owner,
                "activate": bool(activate),
            }
        )

    def busy_state(self, session_id: str, snapshot: ProcessSnapshot | None = None) -> dict[str, Any]:
        """Whether a command (agent or user-typed) is running in the session."""
        session = self.get_session(session_id)
        if not session:
            return {"ok": False, "error": "session not found"}
        return {
            "ok": True,
            "session_id": session_id,
            "busy": session.is_busy(),
            "running": session.has_running_command(snapshot),
        }

    def busy_state_many(self, session_ids: list[str]) -> dict[str, Any]:
        """busy_state for several sessions at once.

        Telling whether a user-typed command runs walks every process on the PC (~10 ms);
        the header polls all its terminals every few seconds, so they share one walk.
        """
        snapshot = functools.cache(_process_snapshot)
        ids = dict.fromkeys(str(sid).strip() for sid in session_ids or [] if str(sid).strip())
        return {"ok": True, "states": {sid: self.busy_state(sid, snapshot) for sid in ids}}

    def _busy_changed(self, session_id: str, busy: bool) -> None:
        """Push terminal_busy when a session's busy flag flips, so the web needs no polling;
        flips closer than _BUSY_PUSH_GAP_S are joined and the latest value goes after the gap."""
        with self._lock:
            if session_id not in self._sessions:
                return  # closed: its shell ending flips busy off, nobody needs that
            state = self._busy_push.setdefault(session_id, {"sent": False, "at": 0.0, "timer": None})
            state["value"] = bool(busy)
            if state["timer"] is not None:
                return
            wait = state["at"] + _BUSY_PUSH_GAP_S - time.monotonic()
            if wait > 0:
                timer = threading.Timer(wait, self._flush_busy, args=(session_id,))
                timer.daemon = True
                state["timer"] = timer
                timer.start()
                return
        self._flush_busy(session_id)

    def _flush_busy(self, session_id: str) -> None:
        with self._lock:
            state = self._busy_push.get(session_id)
            if state is None:
                return
            state["timer"] = None
            value = state["value"]
            if value == state["sent"]:
                return
            state["sent"] = value
            state["at"] = time.monotonic()
        self._emit({"type": "terminal_busy", "session_id": session_id, "busy": value})

    def kill(self, session_id: str, *, push_close: bool = True) -> dict[str, Any]:
        entry = self._pop_session(session_id)
        with self._lock:
            state = self._busy_push.pop(session_id, None)
        if state and state.get("timer") is not None:
            state["timer"].cancel()
        if entry is None:
            return {"ok": False, "error": "session not found"}
        if push_close:
            self._emit({"type": "terminal_close", "session_id": session_id})
        return {"ok": True, "session_id": session_id}

    def _is_shown(self, session_id: str) -> bool:
        """Caller may hold self._lock (reads only)."""
        if not self._visible_terms:
            return True
        return any(session_id in shown for shown in self._visible_terms.values())

    def set_visible_terminals(self, session_ids: list[str] | None, viewer_id: str = "main") -> None:
        """The terminals a window shows now; output to the others is held until shown."""
        with self._lock:
            self._visible_terms[viewer_id or "main"] = {str(s) for s in (session_ids or []) if s}
            entries = [(sid, e.pacer) for sid, e in self._sessions.items()]
            shown = {sid: self._is_shown(sid) for sid, _ in entries}
        for sid, pacer in entries:
            if pacer is not None:
                pacer.set_visible(shown[sid])

    def list_sessions(self) -> list[dict[str, Any]]:
        with self._lock:
            return [e.session.to_dict() for e in self._sessions.values() if not e.session.hidden]

    def get_session(self, session_id: str) -> TerminalSession | None:
        with self._lock:
            entry = self._sessions.get(session_id)
            return entry.session if entry else None

    def _session_status(self, session: TerminalSession) -> dict[str, Any]:
        alive = session.is_alive()
        exit_code = session._exit_code
        return {
            "alive": alive,
            "exit_code": exit_code,
        }

    def _user_write(self, session_id: str, data: str) -> None:
        session = self.get_session(session_id)
        if not session:
            return
        if not session.is_alive():
            with self._lock:
                entry = self._sessions.get(session_id)
            if entry:
                entry.bridge.push_status({"alive": False, "exit_code": session._exit_code})
            return
        try:
            session.write(data)
        except Exception:
            pass

    def write(self, session_id: str, data: str) -> dict[str, Any]:
        session = self.get_session(session_id)
        if not session:
            return {"ok": False, "error": "session not found"}
        try:
            session.write(data)
            return {"ok": True}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    def resize(self, session_id: str, cols: int, rows: int) -> dict[str, Any]:
        session = self.get_session(session_id)
        if not session:
            return {"ok": False, "error": "session not found"}
        session.resize(cols, rows)
        return {"ok": True}

    def request_command(
        self,
        session_id: str,
        command: str,
        source: str = "",
        conv_id: str = "",
        *,
        background: bool = False,
        push_pending: bool = True,
        runner_waits: bool = False,
        timeout_s: float = 300.0,
    ) -> dict[str, Any]:
        cmd = (command or "").strip()
        if not cmd:
            return {"ok": False, "fail": "error", "error": "command must not be empty"}
        session = self.get_session(session_id)
        if not session:
            return {"ok": False, "fail": "not_found", "error": "session not found"}
        if session.is_busy() and not background:
            return {
                "ok": False,
                "fail": "busy",
                "error": "session busy",
                "hint": "Wait for the current command or open another terminal with ducky_terminal_open.",
            }
        request_id = uuid.uuid4().hex[:12]
        cwd = str(getattr(session, "cwd", "") or "")
        offers = _card_offers(cmd, cwd) if push_pending else {}
        lead, asked_by = _lead_of(conv_id) if push_pending else ("", "")
        pending = PendingCommand(
            request_id=request_id,
            session_id=session_id,
            command=cmd,
            source=source or "agent",
            conv_id=conv_id,
            background=background,
            runner_waits=runner_waits and not background,
            timeout_s=float(timeout_s),
            asked=bool(push_pending),
            rule_label=str(offers.get("rule_label") or ""),
            shell=str(getattr(session, "shell", "") or ""),
            cwd=cwd,
            lead_conv_id=lead,
            asked_by=asked_by,
        )
        with self._lock:
            self._pending[request_id] = pending
        if push_pending:
            # A card in the chat that asked (conv_id) and in its lead's chat; with no chat, an
            # entry in the header's background activity list.
            self._emit({"type": "terminal_command_pending", **_pending_dict(pending, source or "ducky_terminal_run")})
            _alert_command(lead or conv_id, cmd, asked_by if lead else "")
        return {"ok": True, "request_id": request_id, "status": "pending_approval"}

    def list_pending(self) -> list[dict[str, Any]]:
        """Unanswered command cards, oldest first (a window that reloads shows them again)."""
        with self._lock:
            rows = [p for p in self._pending.values() if p.asked and not p.decided.is_set()]
        rows.sort(key=lambda p: p.created_at)
        return [_pending_dict(p, p.source) for p in rows]

    def approve_command(self, request_id: str, scope: str = "once", answered_in: str = "") -> dict[str, Any]:
        """scope: once, always (this command, in its chat) or all (everything, in its chat).
        answered_in: the chat whose card was used; the lead's card saves the choice on the lead,
        which covers every member its plan starts."""
        pending = self._take_pending(request_id)
        if pending is None:
            return {"ok": False, "error": "request not found or already decided"}
        saved = _remember_choice(pending, scope, answered_in) if scope in ("always", "all") else "once"
        session = self.get_session(pending.session_id)
        if not session:
            pending.approved = False
            pending.rejection_reason = "session not found"
            pending.decided.set()
            return {"ok": False, "error": "session not found"}
        pending.approved = True
        pending.decided.set()
        if pending.runner_waits:
            return {"ok": True, "request_id": request_id, "saved": saved, "run": {"ok": True, "status": "approved"}}
        if pending.background:
            result = session.run_command(pending.command, background=True)
        else:
            # Run after signaling approval so agent waiters unblock; avoid blocking UI on completion.
            threading.Thread(
                target=self._run_command,
                args=(session, pending.command),
                kwargs={"timeout_s": pending.timeout_s, "conv_id": pending.conv_id},
                daemon=True,
                name=f"terminal-run-{request_id}",
            ).start()
            result = {"ok": True, "status": "running"}
        return {"ok": True, "request_id": request_id, "saved": saved, "run": result}

    def reject_command(self, request_id: str, reason: str = "rejected by user") -> dict[str, Any]:
        pending = self._take_pending(request_id)
        if pending is None:
            return {"ok": False, "error": "request not found or already decided"}
        pending.approved = False
        pending.rejection_reason = reason
        pending.decided.set()
        return {"ok": True, "request_id": request_id, "approved": False, "reason": reason}

    def wait_for_approval(
        self,
        request_id: str,
        timeout_s: float = _APPROVAL_TIMEOUT_S,
        *,
        steer: Callable[[], bool] | None = None,
    ) -> dict[str, Any]:
        """steer: says the person wrote; the card then stays up and Allow runs the command
        with nobody waiting here ({"waiting_for_approval": True})."""
        with self._lock:
            pending = self._pending.get(request_id) or self._decided.pop(request_id, None)
        if pending is None:
            return {"ok": False, "fail": "not_found", "error": "request not found"}
        outcome = _wait_or_steer(pending.decided, max(1.0, float(timeout_s)), steer)
        if outcome == "steer":
            with self._lock:
                left = request_id in self._pending
                if left:
                    pending.runner_waits = False
            if left:
                return {"ok": False, "waiting_for_approval": True}
            pending.decided.wait(5.0)  # answered just now: carry on as if waited
        elif outcome == "timeout":
            self.reject_command(request_id, reason="command not approved (timed out)")
            return {"ok": False, "fail": "timeout", "error": "command not approved (timed out)"}
        if not pending.approved:
            return {"ok": False, "fail": "not_approved", "error": pending.rejection_reason or "command rejected by user"}
        with self._lock:
            self._pending.pop(request_id, None)
        return {"ok": True, "approved": True}

    def run_agent_command(
        self,
        session_id: str,
        command: str,
        *,
        source: str = "",
        conv_id: str = "",
        background: bool = False,
        wait: bool = True,
        approval_timeout_s: float = _APPROVAL_TIMEOUT_S,
        command_timeout_s: float = 300.0,
        auto_approve: bool = False,
        steer_pending: Callable[[], bool] | None = None,
        expect_minutes: float = 0.0,
        early_return: bool = True,
    ) -> dict[str, Any]:
        """auto_approve: the chat said "Allow everything", so no Allow/Deny card. A command
        the chat said "Always allow" for runs without one too.

        expect_minutes: how long the agent says a waited run takes; the chat's tray row gets
        it as a run_expect event (else the median of this command's last runs, estimated).

        wait (not background): the result is the command's exit code; a command that fails
        is ok=False. When the person writes first (steer_pending, default: the chat's steer
        inbox) it comes back early with still_running (or waiting_for_approval) and the
        command goes on as a background one would.

        early_return=False (a workflow step): waits for the end, never steered or overdue. A
        step that came back early let the next step hit "session busy" (Oct 31 2026 release)."""
        runs_here = wait and not background
        steer = steer_pending if steer_pending is not None else _steer_check(conv_id)
        if not early_return:
            steer = None
        known = self.get_session(session_id)
        if not auto_approve and conv_id:
            auto_approve = _chat_always_allows(conv_id, command, str(getattr(known, "cwd", "") or ""))
        # ponytail: reads the terminal's opening folder; a `cd` typed in an earlier command
        # isn't followed (one in this command is).
        cwd = str(getattr(known, "cwd", "") or "")
        try:
            refused = commit_guard.check(command, cwd, conv_id) if known else ""
        except Exception:  # noqa: BLE001 - the guard never blocks a command by failing
            refused = ""
        if refused:
            return {"ok": False, "fail": "foreign_files", "error": refused}
        try:
            # A vague or sprawling commit still runs; the agent is told why it should not.
            warning = commit_guard.warn(command, cwd, conv_id) if known else ""
        except Exception:  # noqa: BLE001
            warning = ""
        out = self._approve_and_run(
            session_id, command, source=source, conv_id=conv_id, background=background,
            runs_here=runs_here, steer=steer, auto_approve=auto_approve,
            approval_timeout_s=approval_timeout_s, command_timeout_s=command_timeout_s,
            expect_minutes=expect_minutes, early_return=early_return,
        )
        if warning:
            out = {**out, "commit_warning": warning}
        return out

    def _approve_and_run(
        self,
        session_id: str,
        command: str,
        *,
        source: str,
        conv_id: str,
        background: bool,
        runs_here: bool,
        steer: Callable[[], bool] | None,
        auto_approve: bool,
        approval_timeout_s: float,
        command_timeout_s: float,
        expect_minutes: float,
        early_return: bool,
    ) -> dict[str, Any]:
        """run_agent_command after the commit guard: the Allow card (or auto-approve), then the run."""
        req = self.request_command(
            session_id,
            command,
            source=source,
            conv_id=conv_id,
            background=background,
            push_pending=not auto_approve,
            runner_waits=runs_here,
            timeout_s=command_timeout_s,
        )
        if not req.get("ok"):
            return req
        request_id = str(req["request_id"])
        if auto_approve:
            approval = self.approve_command(request_id)
        else:
            approval = self.wait_for_approval(request_id, timeout_s=approval_timeout_s, steer=steer)
        if approval.get("waiting_for_approval"):
            return {
                "ok": True,
                "waiting_for_approval": True,
                "request_id": request_id,
                "session_id": session_id,
                "command_id": request_id,
                "note": _STEER_NOTE_APPROVAL,
            }
        if not approval.get("ok"):
            return approval
        session = self.get_session(session_id)
        if session is None:
            return {"ok": False, "fail": "not_found", "error": "session not found"}
        if not runs_here:
            return {
                "ok": True,
                "request_id": request_id,
                "session_id": session_id,
                "status": "running",
            }
        done = threading.Event()
        ran: dict[str, Any] = {}
        expected, estimated = run_durations.expected_ms(source, command, expect_minutes)
        if expected and conv_id:
            self._emit({"type": "run_expect", "conv_id": conv_id, "source": source,
                        "expected_ms": expected, "estimated": estimated})
        started = time.monotonic()

        def work() -> None:
            try:
                ran.update(self._run_command(session, command.strip(), timeout_s=command_timeout_s, conv_id=conv_id))
            except Exception as exc:
                ran.update({"ok": False, "error": str(exc)})
            finally:
                # In the run's own thread: a run returned early (steer) still ends here.
                if "exit_code" in ran:
                    run_durations.record(source, command, time.monotonic() - started)
                if expected and conv_id:
                    self._emit({"type": "run_expect", "conv_id": conv_id, "source": source,
                                "expected_ms": 0, "estimated": False})
                done.set()

        threading.Thread(target=work, daemon=True, name=f"terminal-run-{request_id}").start()
        overdue = max(_OVERDUE_MIN_S, expect_minutes * 180.0) if expect_minutes > 0 and early_return else None
        if overdue is not None and overdue >= command_timeout_s:
            overdue = None
        why = _wait_or_steer(done, overdue, steer)
        if why != "done":
            so_far = agent_command_output(session.read_output_tail(max_chars=_OUTPUT_RING_CHARS),
                                          getattr(session, "run_tag", ""))
            return {
                "ok": True,
                "still_running": True,
                "request_id": request_id,
                "session_id": session_id,
                "command_id": request_id,
                "output_so_far": so_far[-_SO_FAR_CHARS:],
                "note": _STEER_NOTE if why == "steer" else _OVERDUE_NOTE.format(minutes=expect_minutes),
            }
        # A command that printed nothing reports nothing. Falling back to the raw screen gave
        # the agent escape codes and its own echoed command instead (a silent failed push).
        tail = ran["output_tail"] if "output_tail" in ran else agent_command_output(
            session.read_output_tail(max_chars=_OUTPUT_RING_CHARS), getattr(session, "run_tag", ""))
        out: dict[str, Any] = {
            "ok": bool(ran.get("ok")),
            "request_id": request_id,
            "session_id": session_id,
            "output_tail": str(tail),
        }
        if not out["ok"]:
            error = str(ran.get("error") or "command failed")
            fail = _RUN_FAILS.get(error, "error")
            return {**out, "fail": fail, "error": error, **({"hint": ran["hint"]} if ran.get("hint") else {})}
        code = ran.get("exit_code")
        out["exit_code"] = code
        if ran.get("hint"):
            out["hint"] = ran["hint"]
        if code not in (None, 0):
            return {**out, "ok": False, "fail": "exit_nonzero", "error": f"Command failed (exit code {code})."}
        return out

    def _run_command(self, session: TerminalSession, command: str, *, timeout_s: float, conv_id: str = "") -> dict[str, Any]:
        """session.run_command (waited). Test and build runs start at once, never queued behind
        another terminal's: Nov 5 2026 a deploy sat 14 minutes on "Waiting for <chat>'s test or
        build run on this PC". Agent terminals still run below normal priority in a memory cap."""
        return self._run_watched(session, command, timeout_s)

    @staticmethod
    def _run_watched(session: TerminalSession, command: str, timeout_s: float) -> dict[str, Any]:
        """session.run_command; when the terminal's memory peaked at its cap meanwhile, the
        result says so (hint), so the agent runs less at once instead of retrying."""
        def peak_and_cap() -> tuple[int, int]:
            try:
                peak, cap = session.memory_peak_and_cap_mb()
            except Exception:
                return 0, 0
            return (peak, cap) if isinstance(peak, int) and isinstance(cap, int) else (0, 0)

        before, cap = peak_and_cap()
        idle = _idle_beside_uefn(session, command)
        try:
            ran = session.run_command(command, background=False, timeout_s=timeout_s)
        finally:
            if idle:
                session.set_idle(False)
        if cap:
            after, _cap = peak_and_cap()
            if resource_guard.hit_cap(before, after, cap) and isinstance(ran, dict):
                ran = {**ran, "hint": resource_guard.cap_hint(after, cap)}
        return ran

    def read_output(self, session_id: str, max_chars: int = 8000) -> dict[str, Any]:
        session = self.get_session(session_id)
        if not session:
            return {"ok": False, "error": "session not found"}
        return {
            "ok": True,
            "session_id": session_id,
            "output": session.read_output_tail(max_chars=max_chars),
            "busy": session.is_busy(),
            "alive": session.is_alive(),
        }

    def shutdown_all(self) -> None:
        self._reaper_stop.set()
        with self._lock:
            ids = list(self._sessions.keys())
            pending_ids = list(self._pending.keys())
        for rid in pending_ids:
            self.reject_command(rid, reason="panel shutting down")
        for sid in ids:
            self.kill(sid, push_close=False)

    def _take_pending(self, request_id: str) -> PendingCommand | None:
        with self._lock:
            pending = self._pending.pop(request_id, None)
            if pending is not None and pending.runner_waits:
                self._decided[request_id] = pending
                for old in list(self._decided)[:-32]:
                    self._decided.pop(old, None)
        if pending is not None and pending.asked:
            # Every window (and a page replaying the backlog after a reload) drops
            # its Allow pop-up: the question was answered here, or timed out.
            self._emit({
                "type": "terminal_command_decided",
                "request_id": pending.request_id,
                "conv_id": pending.conv_id,
            })
        return pending

    def _pop_session(self, session_id: str) -> "_SessionEntry | None":
        with self._lock:
            entry = self._sessions.pop(session_id, None)
        if entry is None:
            return None
        with self._lock:
            pending_ids = [rid for rid, pending in self._pending.items() if pending.session_id == session_id]
        for rid in pending_ids:
            self.reject_command(rid, reason="terminal closed")
        entry.session.kill()
        entry.bridge.stop()
        return entry


def _idle_beside_uefn(session: Any, command: str) -> bool:
    """With UEFN open, an agent's test or build run goes to idle priority, so UEFN keeps the
    CPU (Nov 5 2026: pytest on 16 cores and an EXE build ran while UEFN crashed). True when
    the terminal was switched and must be switched back."""
    if not getattr(session, "agent", False) or not resource_guard.is_heavy(command):
        return False
    try:
        if not resource_guard.uefn_private_mb():
            return False
        return bool(session.set_idle(True))
    except Exception:
        return False


def _steer_check(conv_id: str) -> Callable[[], bool] | None:
    if not conv_id:
        return None

    def queued() -> bool:
        from frontend.ui_web.steer import has_queued

        return has_queued(conv_id)

    return queued


def _wait_or_steer(event: threading.Event, timeout_s: float | None, steer: Callable[[], bool] | None) -> str:
    """'done' when the event is set, 'timeout' past timeout_s (None: never), 'steer' when
    steer() says the person wrote: asked after _STEER_FIRST_S, then every _STEER_EVERY_S."""
    deadline = None if timeout_s is None else time.monotonic() + timeout_s
    step = _STEER_FIRST_S
    while True:
        if deadline is not None:
            step = min(step, max(0.0, deadline - time.monotonic()))
        if event.wait(step if steer or deadline is not None else None):
            return "done"
        if deadline is not None and time.monotonic() >= deadline:
            return "timeout"
        try:
            if steer and steer():
                return "steer"
        except Exception:
            pass
        step = _STEER_EVERY_S


def _card_offers(command: str, cwd: str) -> dict[str, Any]:
    try:
        from backend.tools.panel.terminal_approval import describe_command

        return describe_command(command, cwd)
    except Exception:
        return {"rule_label": ""}


def _chat_always_allows(conv_id: str, command: str, cwd: str) -> bool:
    try:
        from backend.tools.panel.terminal_approval import allows

        return allows(conv_id, command, cwd)
    except Exception:
        return False


def _remember_choice(pending: PendingCommand, scope: str, answered_in: str = "") -> str:
    chat = pending.conv_id
    if answered_in and answered_in == pending.lead_conv_id:
        chat = answered_in  # "in this chat" on the lead's card means the lead's chat
    if not chat:
        return "once"
    try:
        from backend.tools.panel.terminal_approval import remember

        return remember(chat, pending.command, scope, cwd=pending.cwd)
    except Exception:
        return "once"


def _lead_of(conv_id: str) -> tuple[str, str]:
    """The lead chat a member's card also shows in, and the member's name ('' when none)."""
    if not conv_id:
        return "", ""
    try:
        from backend.tools.panel.permission_prompt import starter_root
        from frontend.ui_web.chats.conversations import load_conversation

        lead = starter_root(conv_id)
        if not lead or lead == conv_id:
            return "", ""
        conv = load_conversation(conv_id)
        name = str(getattr(conv, "ducky_name", "") or getattr(conv, "title", "") or "").strip() if conv else ""
        return lead, name or "Team member"
    except Exception:
        return "", ""


def _alert_command(conv_id: str, command: str, asked_by: str = "") -> None:
    """A command waits for the person's OK: alert (frontend.notifications). The command
    shows in the Windows notification only. Off-thread: naming the chat reads it from disk.
    A member's command alerts its lead's chat (``conv_id``), naming the member (``asked_by``)."""

    def send() -> None:
        try:
            from frontend.notifications import alert
            from frontend.ui_web.chats.conversations import load_conversation

            conv = load_conversation(conv_id) if conv_id else None
            title = str(getattr(conv, "title", "") or "").strip() or "UEFN Ducky"
            status = f"{asked_by} wants to run a command." if asked_by else "Wants to run a command."
            alert(title, status, level="ask", kind="chat" if conv else "",
                  target_id=conv_id if conv else "", detail=command)
        except Exception:
            pass

    threading.Thread(target=send, daemon=True, name="command-alert").start()


def _pending_dict(pending: PendingCommand, source: str) -> dict[str, Any]:
    return {
        "request_id": pending.request_id,
        "session_id": pending.session_id,
        "command": pending.command,
        "shell": pending.shell,
        "cwd": pending.cwd,
        "conv_id": pending.conv_id,
        "source": source,
        "rule_label": pending.rule_label,
        "created_at": pending.created_at,
        "lead_conv_id": pending.lead_conv_id,
        "asked_by": pending.asked_by,
    }


class _SessionEntry:
    __slots__ = ("session", "bridge", "reuse_key", "chat", "run_owned", "pacer")

    def __init__(
        self,
        *,
        session: TerminalSession,
        bridge: TerminalBridge,
        reuse_key: tuple[str, ...] | None = None,
        chat: str = "",
        pacer: OutputPacer | None = None,
    ) -> None:
        self.session = session
        self.bridge = bridge
        # Paces output to the window (frontend.ui_web.lag.output_pacer).
        self.pacer = pacer
        # (chat, shell asked for, folder): an agent opening the same again gets this one.
        self.reuse_key = reuse_key
        # The chat whose agent opened it; "" for a terminal a person opened.
        self.chat = chat
        # A workflow run opened it: closed like an agent's once the run lets go, chat or not.
        self.run_owned = False


def get_terminal_manager() -> TerminalManager:
    global _manager
    with _manager_lock:
        if _manager is None:
            _manager = TerminalManager()
        return _manager
