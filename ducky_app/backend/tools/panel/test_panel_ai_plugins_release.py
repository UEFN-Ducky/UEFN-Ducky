"""Checks run before a plugin is uploaded: clean-clone tests, hidden characters, one release at a time."""

from __future__ import annotations

import io
import subprocess
import sys
import zipfile

import pytest

from backend.tools.panel import panel_ai_plugins as p


def _git(cwd, *args) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


def _repo(root, test_body: str, *, ignored: dict[str, str] | None = None):
    root.mkdir()
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "t@example.com")
    _git(root, "config", "user.name", "t")
    (root / "tests").mkdir()
    (root / "tests" / "test_it.py").write_text(test_body, encoding="utf-8")
    (root / ".gitignore").write_text("tests/evidence/\n__pycache__/\n", encoding="utf-8")
    for rel, text in (ignored or {}).items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text, encoding="utf-8")
    _git(root, "add", ".")
    _git(root, "commit", "-q", "-m", "init")
    return root


_CMD = [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "tests"]


def test_a_test_that_reads_an_ignored_file_fails_in_the_clean_clone_and_names_it(tmp_path) -> None:
    # Media Kit: tests read the git-ignored tests/evidence/ and failed in the workflow's clone 3 times.
    body = (
        "from pathlib import Path\n"
        "def test_evidence():\n"
        "    assert Path('tests/evidence/run.json').read_text()\n"
    )
    repo = _repo(tmp_path / "plug", body, ignored={"tests/evidence/run.json": "{}"})
    assert subprocess.run(_CMD, cwd=repo, capture_output=True).returncode == 0  # passes in the draft
    out = p.clean_clone_test_check(repo, test_cmd=_CMD)
    assert not out["ok"]
    assert out["missing_files"] == ["tests/evidence/"]
    assert out["error"] == ("clean clone: tests read files missing from a clean clone: "
                            "tests/evidence/ → fix before publishing")


def test_a_clean_repo_passes(tmp_path) -> None:
    repo = _repo(tmp_path / "plug", "def test_ok():\n    assert 1 + 1 == 2\n")
    assert p.clean_clone_test_check(repo, test_cmd=_CMD) == {"ok": True, "clone_tests": "passed"}


def test_not_a_repo_is_refused(tmp_path) -> None:
    assert "not a git repository" in p.clean_clone_test_check(tmp_path)["error"]


def test_the_installed_app_says_skipped_never_a_fake_pass(tmp_path, monkeypatch) -> None:
    """Frozen, sys.executable is UEFN-Ducky.exe: '-m pytest' booted a second Ducky that exited
    0 without running a test (Nov 11 2026)."""
    repo = _repo(tmp_path / "plug", "def test_ok():\n    assert False\n")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(p.subprocess, "run", lambda *a, **k: pytest.fail("ran a process"))
    out = p.clean_clone_test_check(repo)
    assert out["ok"] is True and out["clone_tests"] == "skipped" and "no Python" in out["note"]


def _zip(files: dict[str, str]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, text in files.items():
            zf.writestr(name, text.encode("utf-8"))
    return buf.getvalue()


def test_a_raw_control_character_in_a_js_file_fails_with_file_and_offset() -> None:
    # Google 1.1.0: the minifier turned "\u0001" in ui/gmail/list.js into a raw control character.
    out = p.scan_zip_hidden_chars(_zip({"ui/gmail/list.js": "var a='x\x01';", "plugin.json": "{}"}))
    assert not out["ok"]
    assert out["findings"] == [{
        "path": "ui/gmail/list.js", "offset": 8, "char": "U+0001",
        "message": "ui/gmail/list.js offset 8: U+0001 — the Store will refuse this",
    }]


def test_bidi_and_zero_width_are_caught_but_newlines_tabs_and_binaries_are_not(tmp_path) -> None:
    assert not p.scan_zip_hidden_chars(_zip({"a.py": "x = 1  # \u202e"}))["ok"]
    assert not p.scan_zip_hidden_chars(_zip({"b.md": "a\u200bb"}))["ok"]
    clean = _zip({"ui/list.js": "var a = '\\u0001';\r\n\tok\n", "assets/icon.png": "\x01\x02", "x.txt": "\x01"})
    path = tmp_path / "plug.zip"
    path.write_bytes(clean)
    assert p.scan_zip_hidden_chars(path) == {"ok": True}


@pytest.fixture
def releases(tmp_path, monkeypatch):
    from backend.automations import live_runs

    runs: list[dict] = []
    notes: list[tuple[str, str, str]] = []
    monkeypatch.setattr(p, "_releases_dir", lambda: tmp_path / ".releases")
    monkeypatch.setattr(live_runs, "runs", lambda wid="": [r for r in runs if not wid or r["id"] == wid])
    monkeypatch.setattr(p, "_append_step_note", lambda c, s, line: notes.append((c, s, line)))
    return runs, notes


def test_a_running_release_workflow_refuses_a_second_publish(releases, tmp_path) -> None:
    runs, notes = releases
    clone = _repo(tmp_path / "clone", "def test_ok():\n    pass\n")
    p.start_release_run("island-media-kit", "wf-release", "7f3a", chat_id="lead", step_id="s1", clone=str(clone))
    assert notes == [("lead", "s1", "Release workflow run `7f3a` started.")]
    runs.append({"id": "wf-release", "run": "7f3a", "state": "started", "events": []})

    assert p.release_check("island-media-kit") == {"ok": False, "error": "release workflow 7f3a is running"}
    assert p.release_check("island-media-kit", run_id="7f3a")["ok"]  # the workflow itself goes on

    # It ends: failed, with test changes left in its clone.
    for n in range(5):
        (clone / "tests" / f"evidence_{n}.json").write_text("{}", encoding="utf-8")
    runs[0].update(state="error", events=[{"type": "workflow_run", "state": "error", "error": "Run tests: failed"}])
    assert p.release_check("island-media-kit") == {"ok": True}
    line = notes[-1][2]
    assert line.startswith("workflow run 7f3a FAILED: Run tests: failed; clone has 5 uncommitted files")
    assert "tests/evidence_0.json" in line
    assert not (tmp_path / ".releases" / "island-media-kit.json").exists()


def test_nothing_recorded_means_nothing_holds_the_plugin(releases) -> None:
    assert p.release_busy("google") == ""
    assert p.finish_release_run("google") == {"ok": False, "error": "no release run recorded"}
