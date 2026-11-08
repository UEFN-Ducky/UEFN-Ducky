"""Say what a silent coding-agent turn is waiting on.

When the CLI's stream goes quiet the window only knows "no event for a while" and shows
"Thinking about the next step". Most long silences are a tool that has not answered or a
model step that streams nothing (hidden thinking). After ``after_s`` of silence this pushes
a status every ``every_s`` naming which one it is, so the label tells the truth.

Before the CLI starts, ``set_step`` names the setup step running (login check, prompt build...).
A step quiet for ``step_after_s`` is shown; one quiet for ``stuck_after_s`` calls ``on_stuck``
once, unless it was set with ``can_stop=False`` (a login that waits on the person).
"""

from __future__ import annotations

import threading
import time
from typing import Any, Callable

PushFn = Callable[[dict[str, Any]], None]


def _elapsed(seconds: float) -> str:
    total = int(seconds)
    minutes, secs = divmod(total, 60)
    return f"{minutes}m {secs:02d}s" if minutes else f"{secs}s"


def _cli_alive(conv_id: str) -> bool:
    from backend.agent.coding_agents import proc_exec

    proc = proc_exec.get_process(conv_id)
    return proc is not None and proc.poll() is None


class StallNotice:
    """Wraps a turn's push; a background thread reports long silences as status lines."""

    def __init__(
        self,
        conv_id: str,
        run_id: str = "",
        *,
        label: str = "the coding agent",
        after_s: float = 60.0,
        every_s: float = 10.0,
        clock: Callable[[], float] = time.monotonic,
        is_alive: Callable[[], bool] | None = None,
        step_after_s: float = 15.0,
        stuck_after_s: float = 120.0,
        on_stuck: Callable[[str], None] | None = None,
    ) -> None:
        self.conv_id = conv_id
        self.run_id = run_id
        self.label = label
        self.after_s = after_s
        self.every_s = every_s
        self._clock = clock
        self._is_alive = is_alive or (lambda: _cli_alive(conv_id))
        self._last = clock()
        self._pending: list[str] = []
        self._push: PushFn | None = None
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.step_after_s = step_after_s
        self.stuck_after_s = stuck_after_s
        self.on_stuck = on_stuck
        self.step = ""
        self._can_stop = True
        self._fired = False

    def set_step(self, step: str, *, can_stop: bool = True) -> None:
        """Name the setup step now running; "" once the CLI is launching."""
        with self._lock:
            self.step, self._can_stop, self._last = step, can_stop, self._clock()

    def wrap(self, push: PushFn) -> PushFn:
        self._push = push

        def wrapped(event: dict[str, Any]) -> None:
            self.observe(event)
            push(event)

        return wrapped

    def observe(self, event: dict[str, Any]) -> None:
        kind = str(event.get("type") or "")
        with self._lock:
            self._last = self._clock()
            tool = event.get("tool") if isinstance(event.get("tool"), dict) else {}
            if kind == "tool" and tool.get("status") == "pending":
                self._pending.append(str(tool.get("name") or "tool").removeprefix("mcp__uefn__"))
            elif kind == "tool_done" and self._pending:
                # Chips pair positionally: the oldest open one closes first.
                self._pending.pop(0)

    def text(self) -> str:
        """The status to show now, or "" while the stream is not quiet."""
        with self._lock:
            quiet = self._clock() - self._last
            pending = self._pending[0] if self._pending else ""
            step = self.step
        if step:
            return f"Still starting · {step} · {_elapsed(quiet)}" if quiet >= self.step_after_s else ""
        if quiet < self.after_s:
            return ""
        since = _elapsed(quiet)
        if pending:
            return f"Waiting on {pending} · {since}, no answer yet"
        if not self._is_alive():
            return f"No output for {since}: {self.label} has exited, finishing the turn"
        return f"Waiting for the model · {since} with no output ({self.label} is running)"

    def _stuck_step(self) -> str:
        with self._lock:
            if (self.step and self._can_stop and not self._fired and self.on_stuck is not None
                    and self._clock() - self._last >= self.stuck_after_s):
                self._fired = True
                return self.step
        return ""

    def tick(self) -> str:
        stuck = self._stuck_step()
        if stuck:
            try:
                self.on_stuck(stuck)  # type: ignore[misc]
            except Exception:  # noqa: BLE001 - a notice must never break the turn
                pass
            return ""
        text = self.text()
        push = self._push
        if text and push is not None:
            event: dict[str, Any] = {"type": "status", "text": text, "conv_id": self.conv_id}
            if self.run_id:
                event["run_id"] = self.run_id
            try:
                push(event)  # the inner push: a notice is not activity
            except Exception:  # noqa: BLE001 - a notice must never break the turn
                pass
        return text

    def start(self) -> None:
        if self._thread is not None:
            return

        def loop() -> None:
            while not self._stop.wait(self.every_s):
                self.tick()

        self._thread = threading.Thread(target=loop, daemon=True, name=f"stall-{self.conv_id[:8]}")
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        thread, self._thread = self._thread, None
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=2)
