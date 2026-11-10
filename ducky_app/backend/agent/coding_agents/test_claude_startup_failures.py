"""Claude turns that die before (or after) any tool call end with a reason, never kind:other.

Nov 9 2026: 23.6% of Claude turns failed with no tool call and kind:other. A CLI exit with
no message used the raw stream-json tail (or "Claude Code failed") as its end detail.
"""
from __future__ import annotations

import json
import os
import sys
import textwrap

import pytest

from backend.agent.coding_agents.cli_shared import exit_code_text, exit_detail, finalize_cli_turn
from backend.agent.coding_agents.proc_exec import ProcResult, run_streaming_process
from frontend import usage_events as ev

INIT = json.dumps({"type": "system", "subtype": "init", "session_id": "s1",
                   "mcp_servers": [{"name": "uefn", "status": "connected"}]})


def _run(tmp_path, body: str, timeout_s: float = 20):
    script = tmp_path / "fake_claude.py"
    script.write_text(textwrap.dedent(body), encoding="utf-8")
    lines: list[str] = []
    proc = run_streaming_process(argv=[sys.executable, str(script)], cwd=str(tmp_path), env_extra={},
                                 conv_id="fake-" + tmp_path.name, on_line=lines.append, timeout_s=timeout_s)
    return proc, lines


def _finish(proc: ProcResult, *, reply: str = "", error_text: str = ""):
    return finalize_cli_turn(proc=proc, reply=reply, streamed=bool(reply), blocks=[], session_id="s0",
                             new_session="s1", usage={}, agent_label="Claude Code", timeout_s=1,
                             error_text=error_text, stale_session_markers=("no conversation found",))


def test_exit_1_with_no_message_names_the_exit_not_the_json(tmp_path):
    # The top cause: before, the end detail was the init JSON line and kind_of said "other".
    proc, _ = _run(tmp_path, f"""
        import sys
        print({INIT!r}, flush=True)
        sys.exit(1)
    """)
    result = _finish(proc)
    assert not result.ok and result.error == "Claude Code exited with code 1"
    assert ev.kind_of(result.error) == "crash"


def test_mcp_server_that_never_connects_reads_offline(tmp_path):
    # The CLI's own stderr reason reaches the end detail. (A CLI that carries on without the
    # server is the adapter's "Ducky's tools didn't connect", tested in the Anthropic plugin.)
    proc, _ = _run(tmp_path, """
        import sys
        sys.stderr.write('Error: MCP server failed to start\\n')
        sys.exit(1)
    """)
    result = _finish(proc)
    assert result.error == "Claude Code exited with code 1: Error: MCP server failed to start"
    assert ev.kind_of(result.error) == "offline"


def test_resume_past_the_context_window_reads_too_long(tmp_path):
    proc, lines = _run(tmp_path, f"""
        import json
        print({INIT!r}, flush=True)
        print(json.dumps({{"type": "result", "is_error": True, "result": "Prompt is too long"}}), flush=True)
    """)
    error_text = json.loads(lines[-1])["result"]  # the adapter's _parse_result takes the CLI's own text
    result = _finish(proc, error_text=error_text)
    assert not result.ok and ev.kind_of(result.error) == "too_long"


def test_a_hang_with_no_output_reads_timeout(tmp_path):
    proc, lines = _run(tmp_path, "import time; time.sleep(60)", timeout_s=1)
    result = _finish(proc)
    assert proc.timed_out and not lines and result.status == "timeout"
    assert ev.kind_of(result.error) == "timeout"


def test_crash_after_tools_keeps_its_exit_code_last_lines_and_partial_reply(tmp_path):
    code = -1073741819 if os.name == "nt" else 5  # Windows reports it as 3221225477 (0xC0000005)
    proc, lines = _run(tmp_path, f"""
        import json, os, sys
        print({INIT!r}, flush=True)
        print(json.dumps({{"type": "assistant", "message": {{"content": [
            {{"type": "text", "text": "Half done"}},
            {{"type": "tool_use", "id": "t1", "name": "Read", "input": {{}}}}]}}}}), flush=True)
        sys.stderr.write("one\\ntwo\\nat C:\\\\Users\\\\someone\\\\cli.js:1\\nthree\\n")
        sys.stderr.flush()
        os._exit({code})
    """)
    assert len(lines) == 2
    detail = exit_detail(proc, "Claude Code")
    expected = "0xC0000005" if os.name == "nt" else "5"
    assert detail == f"Claude Code exited with code {expected}: two | at ~\\cli.js:1 | three"
    assert "someone" not in detail and ev.kind_of(detail) == "crash"
    # The shared finalizer keeps a partial reply; adapters mark it failed with this detail.
    assert _finish(proc, reply="Half done").reply_text == "Half done"


@pytest.mark.parametrize("code, text", [(0, "0"), (1, "1"), (255, "255"), (3221225477, "0xC0000005"), (-1, "0xFFFFFFFF")])
def test_exit_code_text(code, text):
    assert exit_code_text(code) == text
