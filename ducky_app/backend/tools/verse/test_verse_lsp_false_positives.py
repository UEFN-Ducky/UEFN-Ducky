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
