"""A failed workflow run tells its chat which step failed and how that step's output ended."""

from __future__ import annotations

from backend.tools.panel import panel_automations as pa


def test_the_notice_names_the_failed_step_and_its_last_output(monkeypatch) -> None:
    sent: list[str] = []
    monkeypatch.setattr("backend.automations.store.get_workflow", lambda wid: {"name": "UEFN Ducky Release"})
    monkeypatch.setattr("backend.agent.a2a_client.send_notice", lambda **kw: sent.append(kw["body"]))
    tail = "\n".join(f"line {n}" for n in range(30)) + "\nFAILED test_x.py::test_y\nRELEASE_TESTS_RC_1"
    out = {"ok": False, "error": "Command failed (exit code 1).", "run": "r1", "steps": [
        {"ok": True, "id": "a", "label": "Worktree"},
        {"ok": False, "id": "b", "label": "Tests", "result": {"data": {"ok": False, "output_tail": tail}}},
    ]}

    pa._tell_caller("lead", "wf", out)

    body = sent[0]
    assert "finished: failed: Command failed (exit code 1)." in body
    assert "Failed step: Tests" in body
    assert "FAILED test_x.py::test_y" in body and "RELEASE_TESTS_RC_1" in body
    assert "line 0\n" not in body  # only the end of the output


def test_a_passing_run_adds_no_failure_lines() -> None:
    assert pa._failed_step_lines([{"ok": True, "label": "Tests"}]) == []
