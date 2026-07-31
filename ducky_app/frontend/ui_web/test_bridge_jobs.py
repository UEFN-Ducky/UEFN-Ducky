"""Self-check: bridge jobs return immediately and poll without blocking."""

from __future__ import annotations

import time


def main() -> None:
    from frontend.ui_web import bridge_jobs as bj

    started = bj.job_start(lambda: (time.sleep(0.05), {"ok": True, "n": 7})[1])
    assert started["ok"] and started.get("pending") and started.get("job_id")
    jid = str(started["job_id"])
    assert bj.job_poll(jid).get("pending") is True
    done = bj.job_wait(jid, timeout=2.0, poll_s=0.01)
    assert done.get("pending") is False
    assert done.get("ok") is True and done.get("n") == 7
    assert bj.job_poll(jid).get("ok") is False  # consumed

    # List / non-dict results
    started2 = bj.job_start(lambda: [1, 2, 3])
    out = bj.job_wait(str(started2["job_id"]), timeout=2.0, poll_s=0.01)
    assert out.get("result") == [1, 2, 3]

    # Cancel while pending
    started3 = bj.job_start(lambda: (time.sleep(2.0), {"ok": True})[1])
    jid3 = str(started3["job_id"])
    assert bj.job_cancel(jid3).get("cancelled") is True
    cancelled = bj.job_poll(jid3)
    assert cancelled.get("cancelled") is True and cancelled.get("pending") is False

    # Fast job + another job_start before first poll must NOT expire the result
    # (Cursor API key test is a length check — finishes in ms; JS polls at 100ms).
    started_fast = bj.job_start(lambda: {"ok": True, "detail": "OK"})
    jid_fast = str(started_fast["job_id"])
    time.sleep(0.05)  # let the worker finish
    bj.job_start(lambda: {"ok": True, "n": 1})  # would formerly GC the fast job
    polled_fast = bj.job_poll(jid_fast)
    assert polled_fast.get("pending") is False, polled_fast
    assert polled_fast.get("ok") is True and polled_fast.get("detail") == "OK", polled_fast

    print("test_bridge_jobs: ok")


if __name__ == "__main__":
    main()
