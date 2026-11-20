from __future__ import annotations

import json
from pathlib import Path

import pytest

from frontend import uefn_events, usage_events, usage_stats


@pytest.fixture
def sent(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, dict]]:
    out: list[tuple[str, dict]] = []
    monkeypatch.setattr(uefn_events, "_spawn", lambda fn: fn())
    monkeypatch.setattr(uefn_events, "uefn_release", lambda: "43.10")
    monkeypatch.setattr(usage_stats, "track", lambda name, **p: out.append((name, usage_events.validate(name, p))))
    return out


def test_release_reads_build_version(tmp_path: Path) -> None:
    exe = tmp_path / "Engine" / "Binaries" / "Win64" / "UnrealEditorFortnite-Win64-Shipping.exe"
    exe.parent.mkdir(parents=True)
    (tmp_path / "Engine" / "Build").mkdir()
    (tmp_path / "Engine" / "Build" / "Build.version").write_text(
        json.dumps({"BranchName": "++Fortnite+Release-43.10"}), encoding="utf-8")
    assert uefn_events.release_of(exe) == "43.10"
    assert uefn_events.release_of(tmp_path / "nowhere.exe") == ""


def test_events_carry_words_and_the_release(sent: list) -> None:
    uefn_events.deadline({"reason": "popup", "title": "Save Content"}, heavy=True, steps=3)
    uefn_events.popup("Verse Compile Errors", "build")
    uefn_events.verse_build("errors", 45)
    uefn_events.memory(94.2, 7.6)
    uefn_events.lease(90, gave_up=True)
    assert sent == [
        ("epic.deadline", {"reason": "popup", "heavy": "y", "steps": "many", "uefn": "43.10"}),
        ("uefn.popup", {"kind": "verse_error", "during": "build", "uefn": "43.10"}),
        ("verse.build", {"result": "errors", "took": "1m", "uefn": "43.10"}),
        ("uefn.memory", {"grown": "more", "freed": "16gb", "uefn": "43.10"}),
        ("editor.lease", {"outcome": "gave_up", "waited": "90s"}),
    ]


def test_no_wait_no_lease_event(sent: list) -> None:
    uefn_events.lease(0, gave_up=False)
    assert sent == []


def test_answers_that_read_as_success(sent: list) -> None:
    uefn_events.answer("describe_toolset", "x" * 284817)
    uefn_events.answer("GetDeviceProperties", '{"returnValue": "{}"}')
    uefn_events.answer("execute_python", "Error: VerseStringProperty conversion not implemented")
    uefn_events.answer("GetDeviceProperties", '{"returnValue": "{\\"Width\\": 2}"}')
    assert [(n, p.get("kind") or p.get("size")) for n, p in sent] == [
        ("epic.describe", "more"), ("epic.answer", "empty_props"), ("epic.answer", "verse_string")]


def test_epic_status_once_a_day_per_state(sent: list, monkeypatch: pytest.MonkeyPatch) -> None:
    meta: dict[str, str] = {}
    monkeypatch.setattr(usage_stats, "_meta_get", meta.get)
    monkeypatch.setattr(usage_stats, "_meta_set", meta.__setitem__)
    for reason in ("", "", "port_taken", "weird"):
        uefn_events.epic_status(reason)
    assert [p["state"] for _n, p in sent] == ["online", "port_taken", "other"]
