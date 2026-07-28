"""Self-check: snip/confetti work is queued for the Tk pump, not after()."""

from __future__ import annotations

import queue
import threading


def test_schedule_tk_call_runs_on_pump() -> None:
    from frontend.ui_web import shutdown as sh

    # Drain anything left by other tests.
    while True:
        try:
            sh._tk_call_queue.get_nowait()
        except queue.Empty:
            break

    ran = threading.Event()
    holder: list[str] = []

    def job() -> None:
        holder.append(threading.current_thread().name)
        ran.set()

    sh.schedule_tk_call(job)
    fn = sh._tk_call_queue.get(timeout=1.0)
    fn()
    assert ran.wait(1.0)
    assert holder == [threading.current_thread().name]


if __name__ == "__main__":
    test_schedule_tk_call_runs_on_pump()
    print("test_snip_schedule: ok")
