"""The runtime-smoke report is written even when the smoke fails."""

from __future__ import annotations

import json
import sys

from frontend import runtime_smoke


def test_report_is_written_even_when_the_smoke_fails_later(tmp_path, monkeypatch) -> None:
    monkeypatch.setitem(sys.modules, "backend.agent.hard_rules", None)  # its import now raises
    out = tmp_path / "smoke.json"
    assert runtime_smoke.run(str(out)) == 1
    report = json.loads(out.read_text(encoding="utf-8"))
    assert report["ok"] is False and "native_launcher" not in report
