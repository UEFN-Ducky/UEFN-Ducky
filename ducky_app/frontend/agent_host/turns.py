"""Coding-agent turns run in an agent-host process, not in the app.

App side: ``run_in_host(...)`` hands one turn to the chat's host (``supervisor.assign``) as a
``start_turn`` frame, passes every ``event`` frame to the turn's push (the same push chain an
in-app turn uses), forwards Stop as ``cancel`` and returns when ``turn_ended`` arrives.
Admission, queueing, sessions and the public API stay in ``agent_modes``; only the CLI, its
reader threads and the adapter's line parsing move to the host.

Host side: ``on_frame(channel, frame)`` (called by ``agent_host.main``) starts
``runner.run_coding_agent_message`` on a thread per turn with the same adapter, and sends its
events back, e.g. ``{"op": "event", "conv_id": "c1", "events": [{"type": "text_delta", ...}]}``.

``DUCKY_AGENT_HOST=0`` runs turns in the app (tests); unset, the host is on except under pytest.
"""

from __future__ import annotations

import json
import os
import sys
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable

PushFn = Callable[[dict[str, Any]], None]
READY_S = 30.0
_POLL_S = 0.2


def enabled() -> bool:
    flag = os.environ.get("DUCKY_AGENT_HOST", "").strip()
    if flag:
        return flag != "0"
    return "pytest" not in sys.modules


# --- app side ---------------------------------------------------------------------------------
@dataclass
class _Turn:
    conv_id: str
    run_id: str
    push: PushFn
    host: Any
    done: threading.Event = field(default_factory=threading.Event)
    error: str = ""


_lock = threading.Lock()
_by_run: dict[str, _Turn] = {}
_by_conv: dict[str, _Turn] = {}


INTERRUPTED = "Agent process restarted — send a message to continue"


class HostTurnError(RuntimeError):
    """The turn failed in the host; the host already kept its partial reply."""

    @property
    def interrupted(self) -> bool:
        """The host died mid-turn: its last checkpoint is the turn's reply so far."""
        return str(self) == INTERRUPTED


def _supervisor() -> Any:
    from frontend.agent_host import supervisor

    return supervisor.get() or supervisor.start(on_frame=on_app_frame, on_host_closed=on_host_closed)


def _wait_ready(host: Any, timeout_s: float) -> bool:
    """True once the host connected. False at the timeout, or as soon as its process is gone:
    a host that died at start used to hold every turn for the whole ``READY_S``."""
    deadline = time.monotonic() + timeout_s
    while not host.ready.wait(max(0.0, min(_POLL_S, deadline - time.monotonic()))):
        if not host.alive() or time.monotonic() >= deadline:
            return False
    return host.alive()


def live_turns() -> dict[str, str]:
    """The supervisor's live turn table: ``{conv_id: run_id}`` of turns running in a host."""
    with _lock:
        return {cid: t.run_id for cid, t in _by_conv.items()}


def owns(conv_id: str) -> Any:
    """The host running this chat's turn now, or None."""
    with _lock:
        turn = _by_conv.get(conv_id)
    return turn.host if turn is not None else None


def run_in_host(
    conv_id: str,
    run_id: str,
    text: str,
    *,
    model: str,
    mode: str,
    push: PushFn,
    cancel: threading.Event,
    ready_s: float = READY_S,
) -> str | None:
    """Run one turn in a host and wait for it. None: no host took it (run it here instead);
    "" once it ended; otherwise the host's error text."""
    try:
        sup = _supervisor()
        host = sup.assign(conv_id)
    except Exception:  # noqa: BLE001 - no host process: the app runs the turn as before
        return None
    turn = _Turn(conv_id, run_id, push, host)
    if not _wait_ready(host, ready_s):
        sup.release(conv_id)
        return None
    with _lock:
        _by_run[run_id] = turn
        _by_conv[conv_id] = turn
    try:
        if not host.send("start_turn", conv_id=conv_id, run_id=run_id, text=text, model=model, mode=mode):
            return None
        try:
            from frontend.ui_web import ui_rpc

            ui_rpc.tell_host(conv_id)  # a question already open blocks its tools in the host too
        except Exception:  # noqa: BLE001
            pass
        asked = False
        while not turn.done.wait(_POLL_S):
            if cancel.is_set() and not asked:
                asked = host.send("cancel", conv_id=conv_id, run_id=run_id)
            if not host.alive():
                turn.error = turn.error or INTERRUPTED
                break
        return turn.error
    finally:
        with _lock:
            _by_run.pop(run_id, None)
            if _by_conv.get(conv_id) is turn:
                _by_conv.pop(conv_id, None)
        sup.release(conv_id)
        if turn.error == INTERRUPTED:
            _close_runs([run_id])
        from frontend.ui_web import steer

        # A dead host took its inbox with it: what the person sent goes back to the app's.
        steer.host_turn_ended(conv_id, reclaim=not host.alive())


def kill(conv_id: str) -> bool:
    """A second Stop: the host ends the chat's CLI now (proc_exec.terminate_conv_process)."""
    with _lock:
        turn = _by_conv.get(conv_id)
    return bool(turn) and turn.host.send("cancel", conv_id=conv_id, run_id=turn.run_id, kill=True)


def on_app_frame(host: Any, frame: dict[str, Any]) -> None:
    """Supervisor ``on_frame``: route a host's turn frames to the waiting turn."""
    op = frame.get("op")
    if op == "event":
        conv_id = str(frame.get("conv_id") or "")
        for event in frame.get("events") or []:
            with _lock:
                turn = _by_run.get(str(event.get("run_id") or "")) or _by_conv.get(conv_id)
            if turn is not None and event.get("type") == "user_steered":
                from frontend.ui_web import steer

                steer.delivered(turn.conv_id, str(event.get("steer_id") or ""))
            if turn is not None and event.get("type") == "tool":
                # The turn's chat.turn row lives in this process; runner._ingest's count runs in
                # the host, which has no such row (Oct 27 2026: 258 of 261 turns said 0 tools).
                try:
                    from frontend import usage_stats

                    usage_stats.turn_tool(turn.run_id)
                except Exception:  # noqa: BLE001
                    pass
            if turn is not None:
                # ponytail: runs on the host's reader thread; a slow push delays that host's
                # other chats. Hand off to a queue if that ever shows in the meters.
                try:
                    turn.push(event)
                except Exception:  # noqa: BLE001 - one bad event never stops the reader
                    pass
    elif op == "saved":
        _on_saved(str(frame.get("conv_id") or ""), bool(frame.get("final")))
    elif op == "turn_ended":
        with _lock:
            turn = _by_run.get(str(frame.get("run_id") or ""))
        if turn is not None:
            turn.error = str(frame.get("error") or "")
            turn.done.set()


def _on_screen(conv_id: str) -> bool:
    """Whether some window shows this chat (no window reported yet: all of them do)."""
    panel = sys.modules.get("frontend.ui_web.panel_api")
    if panel is None or not getattr(panel, "_visible_by_viewer", None):
        return True
    return conv_id in panel._visible_union()


def _on_saved(conv_id: str, final: bool) -> None:
    """The host saved this chat. Mid-turn checkpoints need nothing (the window streams the
    turn live; a closed chat reads the database when opened); the turn's final save reloads
    the chat in the windows that show it."""
    if not final or not conv_id or not _on_screen(conv_id):
        return
    from frontend.ui_web.agent_modes import notify_context_changed

    notify_context_changed(conv_id)


def _close_runs(run_ids: list[str]) -> None:
    """The runs' ledger rows become ``interrupted`` (end_run later leaves them so)."""
    try:
        from backend.store.repos import ledger

        ledger.close_stale_runs(run_ids=run_ids)
    except Exception:  # noqa: BLE001 - no database (tests, a file ledger): nothing to close
        pass


def _interrupt(turns: list[_Turn]) -> None:
    for turn in turns:
        turn.error = turn.error or INTERRUPTED
    _close_runs([t.run_id for t in turns])
    for turn in turns:
        turn.done.set()


def mark_host_dead(slot: int) -> list[str]:
    """Supervisor hook (p4e): every turn the host in ``slot`` ran is interrupted. Each keeps its
    checkpoint and session id and its chat says to send a message to continue. Returns those chats."""
    with _lock:
        turns = [t for t in _by_run.values() if getattr(t.host, "slot", None) == slot]
    _interrupt(turns)
    return [t.conv_id for t in turns]


def on_host_closed(host: Any, reason: str) -> None:
    """Supervisor ``on_host_closed``: that host's turns are interrupted at once."""
    with _lock:
        turns = [t for t in _by_run.values() if t.host is host]
    _interrupt(turns)


# --- host side --------------------------------------------------------------------------------
_cancels: dict[str, threading.Event] = {}


def _jsonable(event: dict[str, Any]) -> dict[str, Any]:
    """The channel's writer thread dies on an unserialisable frame: stringify odd values here."""
    return json.loads(json.dumps(event, default=str))


def _sender(channel: Any) -> Callable[[dict[str, Any]], None]:
    from frontend.agent_host import main as host_main

    feed = getattr(host_main, "feed", None)  # the host's batcher (10 text batches/s per chat)
    if callable(feed):
        return feed
    return lambda event: channel.send("event", conv_id=event.get("conv_id", ""), events=[event])


def _flush() -> None:
    from frontend.agent_host import main as host_main

    batcher = getattr(host_main, "_batcher", None)
    if batcher is not None:
        batcher.flush()


def _report_saves(channel: Any) -> None:
    """The host writes its turns' checkpoints and replies to ducky.db itself; each save is told."""
    from backend.agent.coding_agents import runner

    def saved(conv_id: str, final: bool) -> None:
        if final:
            _flush()  # the turn's last events reach the app before its reload
        channel.send("saved", conv_id=conv_id, final=final)

    runner.on_saved = saved


def _host_turn(channel: Any, frame: dict[str, Any]) -> None:
    conv_id, run_id = str(frame["conv_id"]), str(frame["run_id"])
    cancel = _cancels[run_id]
    send = _sender(channel)

    def push(event: dict[str, Any]) -> None:
        send(_jsonable({"conv_id": conv_id, "run_id": run_id, **event}))

    error = ""
    conv = None
    try:
        from backend.agent.coding_agents.runner import run_coding_agent_message
        from frontend.settings import PanelSettings, apply_workspace_env
        from frontend.ui_web.project_chats import load_conversation

        apply_workspace_env(PanelSettings.load().uefn_project_root)
        conv = load_conversation(conv_id)
        if conv is None:
            raise RuntimeError("Conversation not found")
        run_coding_agent_message(conv, str(frame.get("text") or ""), model=str(frame.get("model") or ""),
                                 push=push, run_id=run_id, cancel=cancel, mode=str(frame.get("mode") or "agent"))
    except Exception as exc:  # noqa: BLE001 - the app reports it; the partial reply is kept here
        error = str(exc) or type(exc).__name__
        if conv is not None:
            try:
                from backend.agent.coding_agents.runner import checkpoint_coding_turn

                partial = next((m for m in reversed(conv.messages)
                                if m.get("role") == "assistant" and m.get("run_id") == run_id), {})
                checkpoint_coding_turn(conv, agent_id=str(getattr(conv, "coding_agent", "") or ""), run_id=run_id,
                                       blocks=partial.get("blocks"), reply=partial.get("text", ""), error=error,
                                       requested_mode=str(frame.get("mode") or "agent"), effective_mode="")
            except Exception:  # noqa: BLE001
                pass
    finally:
        _cancels.pop(run_id, None)
        from frontend.agent_host import steer_host

        steer_host.set_question(conv_id, False)
        _flush()
        channel.send("turn_ended", conv_id=conv_id, run_id=run_id, error=error)


def on_frame(channel: Any, frame: dict[str, Any]) -> None:
    """Host main loop's handler for app frames other than ``shutdown``."""
    from frontend.agent_host import steer_host

    op = frame.get("op")
    if op == "steer_post":
        steer_host.post(str(frame.get("conv_id") or ""), str(frame.get("steer_id") or ""),
                        str(frame.get("text") or ""))
    elif op == "question_answer":
        steer_host.set_question(str(frame.get("conv_id") or ""), bool(frame.get("open")))
    elif op == "start_turn":
        steer_host.enable()
        _report_saves(channel)
        run_id = str(frame.get("run_id") or "")
        if not run_id or run_id in _cancels:
            return
        _cancels[run_id] = threading.Event()
        threading.Thread(target=_host_turn, args=(channel, frame), name=f"host-turn-{run_id[:8]}",
                         daemon=True).start()
    elif op == "cancel":
        event = _cancels.get(str(frame.get("run_id") or ""))
        if event is not None:
            event.set()
        if frame.get("kill"):
            from backend.agent.coding_agents import proc_exec

            proc_exec.terminate_conv_process(str(frame.get("conv_id") or ""))
