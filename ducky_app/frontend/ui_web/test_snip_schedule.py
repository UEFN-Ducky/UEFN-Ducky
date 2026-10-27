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


def test_pump_owns_its_root_and_serves_other_threads() -> None:
    # The old pump update()d a root the blocked main thread made: 'main thread is not in main
    # loop' a second in, so queued work never ran. The pump thread now makes and loops the root.
    import tkinter as tk

    from frontend.ui_web import shutdown as sh

    made_on: list[str] = []

    def make_root():
        made_on.append(threading.current_thread().name)
        root = tk.Tk()
        root.withdraw()
        return root

    root = sh.start_tk_pump(make_root)
    assert root is not None and made_on == ["tk-pump"]
    ran = threading.Event()
    seen: list[str] = []
    sh.schedule_tk_call(lambda: (seen.append(threading.current_thread().name), ran.set()))
    assert ran.wait(3.0) and seen == ["tk-pump"]
    root.after(0, lambda: None)  # another thread's Tk call is marshalled, not refused
    done = threading.Event()
    sh.schedule_tk_call(lambda: (root.quit(), done.set()))
    assert done.wait(3.0)


if __name__ == "__main__":
    test_schedule_tk_call_runs_on_pump()
    print("test_snip_schedule: ok")
