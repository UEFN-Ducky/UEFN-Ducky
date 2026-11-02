"""The agent's offline Verse check: right project, never holds a turn, newest files first."""

from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path

from backend.tools.verse import verse_diagnostics as vd


def test_check_uses_the_project_the_agent_writes_into(monkeypatch, tmp_path: Path) -> None:
    # Oct 17 2026: a chat in Roguelike wrote its .verse there; the check read Settings' project.
    chat_project = tmp_path / "Roguelike"
    chat_project.mkdir()
    monkeypatch.setattr("backend.bridge.client.workspace_roots", lambda: [str(chat_project)])
    assert Path(vd._project_root()) == chat_project


def test_a_stuck_scan_is_stopped_and_reported(monkeypatch, tmp_path: Path) -> None:
    from frontend.ui_web.verse_editor.lsp import diagnostics_cache, diagnostics_scan

    released = threading.Event()
    aborted: list[bool] = []
    monkeypatch.setattr(diagnostics_scan, "scan_project_verse_diagnostics", lambda *a, **k: released.wait(10))
    monkeypatch.setattr(diagnostics_scan, "abort_active_scan", lambda: (aborted.append(True), released.set()))
    monkeypatch.setattr(vd, "_AGENT_SCAN_SECONDS", 0.2)
    started = time.monotonic()
    assert vd._scan_within_limit(str(tmp_path), False) is False
    assert time.monotonic() - started < 5 and aborted == [True]

    monkeypatch.setattr(vd, "verse_editor_enabled", lambda: True)
    monkeypatch.setattr(vd, "_project_root", lambda: str(tmp_path))
    monkeypatch.setattr("frontend.project_kind.is_folder_project", lambda _root: False)
    monkeypatch.setattr(vd, "_scan_within_limit", lambda root, full: False)
    monkeypatch.setattr(diagnostics_cache, "stale_keys", lambda root: ["content/duckyaitest/hunt.verse"])
    monkeypatch.setattr(diagnostics_cache, "load_for_ui", lambda root: {"files": []})
    monkeypatch.setattr("frontend.ui_web.verse_editor.panel_events.push_verse_diagnostics_sync", lambda *a, **k: None)
    out = json.loads(vd.workspace_list_verse_errors())
    assert out["complete"] is False
    assert out["unchecked"] == ["content/duckyaitest/hunt.verse"]
    assert "never report such a file as clean" in out["note"]


def test_a_build_that_never_answers_names_the_popup(monkeypatch) -> None:
    import pytest

    class Client:
        connected = True

        def compile_project(self):
            raise TimeoutError("Verse workflow request timed out: compileProject")

    monkeypatch.setattr("frontend.ui_web.verse_editor.workflow.client.get_workflow_client", lambda: Client())
    monkeypatch.setattr("backend.tools.core.uefn_popups.list_popups",
                        lambda: [{"title": "Message", "blocking": True}])
    with pytest.raises(ValueError, match="'Message'.*uefn_popups"):
        vd.workspace_compile_verse()


def test_scan_checks_the_newest_files_first(monkeypatch, tmp_path: Path) -> None:
    from frontend.ui_web.verse_editor.lsp import diagnostics_cache, diagnostics_scan

    content = tmp_path / "Content"
    content.mkdir()
    for i, name in enumerate(["a.verse", "b.verse", "new.verse"]):
        f = content / name
        f.write_text("x\n", encoding="utf-8")
        os.utime(f, (1_000_000 + i * 100, 1_000_000 + i * 100))
    diagnostics_cache.clear(str(tmp_path))
    opened: list[str] = []

    class Session:
        def __init__(self, *_a, **_k):
            pass

        def start(self):
            return None

        def request(self, method, *_a, **_k):
            return {}

        def notify(self, method, params):
            if method == "textDocument/didOpen":
                opened.append(params["textDocument"]["uri"].rsplit("/", 1)[-1])

        def stop(self):
            return None

        def is_alive(self):
            return True

        def file_uri(self, rel: str = "") -> str:
            return f"file:///{rel}"

        def diagnostic_uri_count(self):
            return 99

        def published_diagnostics(self, _uri):
            return []

        def diagnostics_for_uri(self, _uri):
            return []

        def syntax_errors(self, _uri):
            return []

    monkeypatch.setattr(diagnostics_scan, "LspStdioSession", Session)
    monkeypatch.setattr(
        diagnostics_scan,
        "discover_verse_workspace",
        lambda _root: {"workspace_folders": [{"path": str(tmp_path), "name": "t"}], "watch_files": []},
    )
    diagnostics_scan.scan_project_verse_diagnostics(str(tmp_path), full=False)
    assert opened[0] == "new.verse"
