"""24h rolling log — age trim and support dump redact."""

from __future__ import annotations

import json
import time
from pathlib import Path

import frontend.error_log as error_log
from frontend.support_dump import _redact, format_support_dump


def test_trim_drops_older_than_24h(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(error_log, "default_app_data_dir", lambda: tmp_path)
    path = tmp_path / "errors.jsonl"
    old = {"ts": time.time() - error_log.MAX_AGE_S - 10, "source": "old", "message": "gone"}
    fresh = {"ts": time.time(), "source": "new", "message": "keep"}
    path.write_text(json.dumps(old) + "\n" + json.dumps(fresh) + "\n", encoding="utf-8")
    error_log.trim()
    rows = error_log.read_errors()
    assert [r["message"] for r in rows] == ["keep"]


def test_activity_roundtrip(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(error_log, "default_app_data_dir", lambda: tmp_path)
    error_log.record_activity("panel", "hello")
    rows = error_log.read_activity()
    assert rows[0]["message"] == "hello"
    assert rows[0]["source"] == "panel"


def test_redact_strips_keys() -> None:
    assert "[redacted]" in _redact("Authorization Bearer sk-abc123456789")
    assert "sk-abc123456789" not in _redact("key sk-abc123456789 leftover")


def test_support_dump_has_header(monkeypatch) -> None:
    monkeypatch.setattr("frontend.support_dump._listener_line", lambda: "offline")
    monkeypatch.setattr("frontend.support_dump._plugin_lines", lambda: ["  openai 1.0.29 on"])
    monkeypatch.setattr("frontend.support_dump._agent_lines", lambda: ["  Codex: available"])
    monkeypatch.setattr("frontend.support_dump._key_line", lambda: "openai=no")
    text = format_support_dump(max_chars=4000)
    assert text.startswith("UEFN-Ducky support dump")
    assert "no chats, keys, or personal files" in text
    assert "Plugins:" in text
    assert "openai 1.0.29 on" in text
    assert "Agents:" in text
