"""proc_exec — timeout_s <= 0 must not wall-clock-kill the CLI."""

from __future__ import annotations

import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.agent.coding_agents import proc_exec


def test_zero_timeout_waits_for_process_exit(tmp_path, monkeypatch):
    """A short-lived process must finish cleanly when timeout_s=0 (unlimited)."""
    script = tmp_path / "echo.py"
    script.write_text("print('ok')\n", encoding="utf-8")
    lines: list[str] = []
    result = proc_exec.run_streaming_process(
        argv=[sys.executable, str(script)],
        cwd=str(tmp_path),
        env_extra={},
        conv_id="test-unlimited",
        on_line=lines.append,
        timeout_s=0.0,
        cancel=None,
    )
    assert result.timed_out is False
    assert result.cancelled is False
    assert result.returncode == 0
    assert any("ok" in ln for ln in lines)


def test_positive_timeout_still_kills(tmp_path, monkeypatch):
    script = tmp_path / "sleep.py"
    script.write_text("import time; time.sleep(30)\n", encoding="utf-8")
    result = proc_exec.run_streaming_process(
        argv=[sys.executable, str(script)],
        cwd=str(tmp_path),
        env_extra={},
        conv_id="test-timeout",
        on_line=lambda _ln: None,
        timeout_s=0.3,
        cancel=None,
    )
    assert result.timed_out is True


def test_cancel_still_kills_with_unlimited_timeout(tmp_path):
    script = tmp_path / "sleep.py"
    script.write_text("import time; time.sleep(30)\n", encoding="utf-8")
    cancel = threading.Event()

    def _cancel_soon() -> None:
        time.sleep(0.2)
        cancel.set()

    threading.Thread(target=_cancel_soon, daemon=True).start()
    result = proc_exec.run_streaming_process(
        argv=[sys.executable, str(script)],
        cwd=str(tmp_path),
        env_extra={},
        conv_id="test-cancel",
        on_line=lambda _ln: None,
        timeout_s=0.0,
        cancel=cancel,
    )
    assert result.cancelled is True
    assert result.timed_out is False
