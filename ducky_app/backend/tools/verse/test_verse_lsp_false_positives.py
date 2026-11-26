"""The offline Verse check stops flagging valid event/listenable .Await() (Nov 2 2026: 26 such
verse-lsp errors on code UEFN's build accepted), and a newer clean build outranks verse-lsp."""

from __future__ import annotations

import os
import time
from pathlib import Path

from backend.tools.verse import verse_diagnostics
from frontend.ui_web.verse_editor.lsp import diagnostics_scan as scan

# Await lines from a game controller the UEFN compiler builds clean.
AWAIT_LINES = [
    "                StopEvent.Await()",
    "                    V := if (Old := S.Vehicle?) then Old else S.SpawnedEvent.Await()",
    "            loop { OnRunStarting(Runner.RunStartingEvent.Await()) }",
    "            loop { Runner.PlayAgain(Input.RetryEvent.Await()) }",
    "            Agent := D.WreckedEvent.Await()",
]


def _diag(line: int, col: int, message: str) -> dict:
    return {"severity": 1, "message": message, "range": {"start": {"line": line, "character": col}}}


def test_await_internal_function_errors_are_dropped() -> None:
    diags = [
        _diag(i, text.index("Await"), "Invalid access of internal function `(/Verse.org/Concurrency/awaitable:)Await`")
        for i, text in enumerate(AWAIT_LINES)
    ]
    diags.append(_diag(9, 4, "Invalid access of internal function `listenable(agent).Await`"))
    assert scan._count_severities(diags) == (0, 0)
    assert scan._diagnostics_to_items(diags) == []


def test_real_errors_still_count() -> None:
    diags = [
        _diag(1, 0, "Unknown identifier `Foo`"),
        _diag(2, 0, "Invalid access of internal function `MyHelper`"),
    ]
    assert scan._count_severities(diags) == (2, 0)
    assert len(scan._diagnostics_to_items(diags)) == 2


def test_a_newer_clean_build_outranks_lsp(tmp_path: Path) -> None:
    root = str(tmp_path)
    verse = tmp_path / "Content" / "Verse" / "a.verse"
    verse.parent.mkdir(parents=True)
    verse.write_text("x", encoding="utf-8")
    old = time.time() - 60
    os.utime(verse, (old, old))
    flagged = {"path": "content/verse/a.verse", "errors": 2, "warnings": 0, "items": []}
    clean = {"path": "content/verse/b.verse", "errors": 0, "warnings": 1, "items": []}

    verse_diagnostics._LAST_COMPILE[root] = (time.time(), 0)
    payload = {"files": [flagged, clean]}
    verse_diagnostics._defer_to_clean_build(root, payload)
    assert payload["files"] == [clean] and payload["lsp_only"] == [flagged]

    # Edited after the build, or the build had errors: verse-lsp's errors stand.
    for built in ((old - 60, 0), (time.time(), 3)):
        verse_diagnostics._LAST_COMPILE[root] = built
        payload = {"files": [flagged]}
        verse_diagnostics._defer_to_clean_build(root, payload)
        assert payload == {"files": [flagged]}
    verse_diagnostics._LAST_COMPILE.pop(root)


def test_on_a_mac_diagnostics_point_to_the_pc_instead_of_failing(tmp_path: Path, monkeypatch) -> None:
    import json
    import sys

    from frontend import ducky_link

    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(ducky_link, "list_duckies", lambda: {"ok": True, "devices": [
        {"keyId": "k_pc", "name": "Tas's PC", "live": True, "mine": False}]})
    monkeypatch.setattr(verse_diagnostics, "verse_editor_enabled", lambda: True)
    monkeypatch.setattr(verse_diagnostics, "_project_root", lambda: str(tmp_path))
    monkeypatch.setattr("frontend.project_kind.is_folder_project", lambda _root: False)

    def no_scan(*_a, **_kw):
        raise AssertionError("no verse-lsp scan on a Mac")

    monkeypatch.setattr(verse_diagnostics, "_scan_within_limit", no_scan)
    out = json.loads(verse_diagnostics.workspace_list_verse_errors())
    assert out["ok"] is True and out["lsp"] == "remote" and out["complete"] is False
    assert out["note"].startswith("Verse diagnostics run on Tas's PC")
    assert out["open_remote"] == [{"key_id": "k_pc", "name": "Tas's PC"}]
    for tool in (verse_diagnostics.workspace_compile_verse, verse_diagnostics.workspace_push_verse_changes):
        built = json.loads(tool())
        assert built["ok"] is False and built["error"].startswith("UEFN runs on Windows"), tool
    monkeypatch.setattr(sys, "platform", "win32")
    assert verse_diagnostics.diagnostics_on_pc() is None
