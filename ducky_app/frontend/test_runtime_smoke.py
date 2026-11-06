"""The runtime-smoke report carries the fresh-settings native_launcher value."""

from __future__ import annotations

import json
import sys

from frontend import runtime_smoke


def test_fresh_settings_native_launcher_is_off() -> None:
    assert runtime_smoke.native_launcher_default() == "off"


def test_report_has_the_key_even_when_the_smoke_fails_later(tmp_path, monkeypatch) -> None:
    monkeypatch.setitem(sys.modules, "backend.agent.hard_rules", None)  # its import now raises
    out = tmp_path / "smoke.json"
    assert runtime_smoke.run(str(out)) == 1
    report = json.loads(out.read_text(encoding="utf-8"))
    assert report["ok"] is False and report["native_launcher"] == "off"
