"""Read, search, edit and git tools that replace shell commands for coding agents."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from backend.tools.core import system, workspace_code as wc
from backend.workspace import runtime
from backend.workspace.writer import ProjectWriter


class RecordingJournal:
    def __init__(self) -> None:
        self.records: list = []

    def record(self, record):
        self.records.append(record)
        return {"run_id": "r", "seq": len(self.records)}

    def note_read(self, path: str, content_hash: str, project_root: str = "") -> None:
        pass


@pytest.fixture
def project(tmp_path: Path, monkeypatch):
    root = tmp_path / "Proj"
    (root / "src" / "pkg").mkdir(parents=True)
    (root / "node_modules" / "dep").mkdir(parents=True)
    (root / "src" / "app.py").write_text(
        "import os\n\n\nclass Store:\n    def load(self):\n        return 1\n\n    def save(self):\n        return 2\n\n\ndef main():\n    Store().load()\n",
        encoding="utf-8",
    )
    (root / "src" / "pkg" / "util.ts").write_text(
        "export interface Opts {\n  a: number;\n}\n\nexport function helper(x: number): number {\n  return x + 1;\n}\n\nexport const twice = (x: number) => x * 2;\n",
        encoding="utf-8",
    )
    (root / "README.md").write_text("# Title\n\nIntro\n\n## Setup\n\nRun it.\n", encoding="utf-8")
    (root / "node_modules" / "dep" / "index.js").write_text("const Store = 1;\n", encoding="utf-8")
    (root / "logo.png").write_bytes(b"\x89PNG\r\n\x1a\n\0\0Store")

    def resolve(rel: str) -> str:
        rel = (rel or ".").replace("\\", "/").strip("/")
        return str(root if rel in ("", ".") else root / rel)

    journal = RecordingJournal()
    runtime.reset_for_tests(ProjectWriter.for_root(str(root), journal=journal))
    # A folder project: the island rules (Content/ only, no .py) do not apply.
    monkeypatch.setattr("backend.workspace.writer._is_folder_project_root", lambda _root: True)
    monkeypatch.setattr(wc, "resolve_workspace_path", resolve)
    monkeypatch.setattr(system, "resolve_workspace_path", resolve)
    monkeypatch.setattr(wc, "tool_json", lambda payload, pretty=False: json.dumps(payload))
    monkeypatch.setattr(system, "tool_json", lambda payload, pretty=False: json.dumps(payload))
    monkeypatch.setattr("frontend.ui_web.verse_editor.agent_sync.emit_for_bridge_tool", lambda *a, **k: None)
    yield root, journal
    runtime.reset_for_tests(None)


def _call(fn, *args, **kwargs) -> dict:
    return json.loads(fn(*args, **kwargs))


# ---------------------------------------------------------------- reads


def test_read_file_returns_only_the_lines_asked_for(project) -> None:
    out = _call(system.workspace_read_file, "src/app.py", start_line=4, end_line=6, line_numbers=True)
    assert out["content"] == "4| class Store:\n5|     def load(self):\n6|         return 1"
    assert out["total_lines"] == 13 and (out["start_line"], out["end_line"]) == (4, 6)
    whole = _call(system.workspace_read_file, "src/app.py")
    assert whole["content"].startswith("import os") and "start_line" not in whole and "more" not in whole


def test_read_without_range_stops_at_the_cap(project, monkeypatch) -> None:
    root, _ = project
    (root / "big.txt").write_text("".join(f"line {n}\n" for n in range(1, 11)), encoding="utf-8")
    monkeypatch.setattr(wc, "READ_LINE_CAP", 4)
    out = _call(system.workspace_read_file, "big.txt")
    assert out["content"].splitlines() == ["line 1", "line 2", "line 3", "line 4"]
    assert "start_line=5" in out["more"]


def test_read_files_reads_several_and_reports_missing(project) -> None:
    out = _call(wc.workspace_read_files, ["src/app.py", "nope.py", "README.md"], start_line=1, end_line=1)
    by_path = {f["path"]: f for f in out["files"]}
    assert by_path["src/app.py"]["content"] == "import os"
    assert by_path["README.md"]["content"] == "# Title"
    assert "error" in by_path["nope.py"]


def test_outline_lists_symbols_with_their_line_spans(project) -> None:
    py = _call(wc.workspace_file_outline, "src/app.py")["symbols"]
    assert [(s["kind"], s["name"], s["line"], s["end_line"]) for s in py] == [
        ("class", "Store", 4, 9),
        ("function", "load", 5, 6),
        ("function", "save", 8, 9),
        ("function", "main", 12, 13),
    ]
    ts = {s["name"]: s["kind"] for s in _call(wc.workspace_file_outline, "src/pkg/util.ts")["symbols"]}
    assert ts == {"Opts": "type", "helper": "function", "twice": "function"}
    md = [s["name"] for s in _call(wc.workspace_file_outline, "README.md")["symbols"]]
    assert md == ["Title", "Setup"]


def test_tree_skips_dependencies(project) -> None:
    tree = _call(wc.workspace_tree, ".", depth=3)["tree"]
    assert "src/" in tree and "  pkg/" in tree and "    util.ts" in tree
    assert "node_modules" not in tree


# ---------------------------------------------------------------- search / find


def test_search_finds_text_and_skips_binaries_and_dependencies(project) -> None:
    out = _call(wc.workspace_search, "store")
    assert {(m["path"], m["line"]) for m in out["matches"]} == {("src/app.py", 4), ("src/app.py", 13)}
    assert out["total_hits"] == 2 and out["files_with_hits"] == 1


def test_search_modes_glob_regex_and_context(project) -> None:
    files = _call(wc.workspace_search, "return", output_mode="files")["files"]
    assert {f["path"] for f in files} == {"src/app.py", "src/pkg/util.ts"}
    only_ts = _call(wc.workspace_search, "return", glob="*.ts")
    assert [m["path"] for m in only_ts["matches"]] == ["src/pkg/util.ts"]
    hit = _call(wc.workspace_search, r"def \w+\(self\)", regex=True, context=1, case_sensitive=True)["matches"][0]
    assert hit["line"] == 5 and hit["before"] == ["class Store:"] and hit["after"] == ["        return 1"]
    assert _call(wc.workspace_search, "return", output_mode="count")["total_hits"] == 3
    with pytest.raises(ValueError):
        wc.workspace_search("(", regex=True)


def test_find_matches_names_paths_and_globs(project) -> None:
    assert _call(wc.workspace_find, "*.ts")["files"] == ["src/pkg/util.ts"]
    assert _call(wc.workspace_find, "src/**/*.py")["files"] == ["src/app.py"]
    assert _call(wc.workspace_find, "**/*.ts")["files"] == ["src/pkg/util.ts"]
    assert _call(wc.workspace_find, "util")["files"] == ["src/pkg/util.ts"]
    assert "node_modules/dep/index.js" not in _call(wc.workspace_find, "*.js")["files"]


# ---------------------------------------------------------------- edits


def test_edit_file_replaces_exact_text_through_the_pipeline(project) -> None:
    root, journal = project
    out = _call(wc.workspace_edit_file, "src/app.py", old_text="return 2", new_text="return 3")
    assert "return 3" in (root / "src" / "app.py").read_text(encoding="utf-8")
    assert out["relative_path"] == "src/app.py" and out["replacements"] == 1
    assert journal.records[-1].tool == "workspace_edit_file"


def test_edit_file_refuses_missing_or_ambiguous_text(project) -> None:
    root, _ = project
    before = (root / "src" / "app.py").read_text(encoding="utf-8")
    with pytest.raises(ValueError, match="not in the file"):
        wc.workspace_edit_file("src/app.py", old_text="return 99", new_text="x")
    with pytest.raises(ValueError, match="appears 2 times"):
        wc.workspace_edit_file("src/app.py", old_text="        return", new_text="        yield")
    assert (root / "src" / "app.py").read_text(encoding="utf-8") == before
    out = _call(wc.workspace_edit_file, "src/app.py", old_text="        return", new_text="        yield", replace_all=True)
    assert out["replacements"] == 2



def test_edit_tools_take_path_like_the_read_tool(project) -> None:
    # Live team run: an agent sent path= to workspace_edit_file (workspace_read_file takes it)
    # and failed validation before it could edit.
    root, _ = project
    _call(wc.workspace_edit_file, path="src/app.py", old_text="return 2", new_text="return 3")
    assert "return 3" in (root / "src" / "app.py").read_text(encoding="utf-8")
    _call(wc.workspace_replace_lines, path="README.md", start_line=1, end_line=1, new_text="# Renamed")
    assert (root / "README.md").read_text(encoding="utf-8").startswith("# Renamed\n")
    with pytest.raises(ValueError, match="different files"):
        wc.workspace_edit_file("src/app.py", path="README.md", old_text="x", new_text="y")
    with pytest.raises(ValueError, match="relative_path is required"):
        wc.workspace_edit_file(old_text="x", new_text="y")


def test_empty_old_text_says_how_to_append(project) -> None:
    # The agent asked to add a line had no way to anchor it and left a blank first line.
    root, _ = project
    lines = len((root / "README.md").read_text(encoding="utf-8").splitlines())
    with pytest.raises(ValueError, match=f"workspace_replace_lines with start_line={lines + 1}, end_line={lines}"):
        wc.workspace_edit_file("README.md", old_text="", new_text="tail")


def test_empty_old_text_creates_or_fills_an_empty_file(project) -> None:
    # Live team run r9: both writers were told to add a line with this tool, "creating the
    # file if missing"; it refused, and their newline workaround left a blank first line.
    root, journal = project
    out = _call(wc.workspace_edit_file, "shared.txt", old_text="", new_text="A was here\n")
    assert (root / "shared.txt").read_text(encoding="utf-8") == "A was here\n"
    assert out["created"] is True and journal.records[-1].tool == "workspace_edit_file"
    (root / "empty.txt").write_text("", encoding="utf-8")
    _call(wc.workspace_edit_file, "empty.txt", old_text="", new_text="first\n")
    assert (root / "empty.txt").read_text(encoding="utf-8") == "first\n"
    with pytest.raises(ValueError, match="has text"):
        wc.workspace_edit_file("shared.txt", old_text="", new_text="again")
    assert (root / "shared.txt").read_text(encoding="utf-8") == "A was here\n"


def test_replace_lines_appends_on_a_new_line(project) -> None:
    root, _ = project
    path = root / "tail.txt"
    path.write_text("A was here", encoding="utf-8")
    _call(wc.workspace_replace_lines, "tail.txt", start_line=2, end_line=1, new_text="B got: maple")
    assert path.read_text(encoding="utf-8") == "A was here\nB got: maple"
    empty = root / "empty.txt"
    empty.write_text("", encoding="utf-8")
    _call(wc.workspace_replace_lines, "empty.txt", start_line=1, end_line=0, new_text="first\n")
    assert empty.read_text(encoding="utf-8") == "first\n"


def test_multi_edit_is_all_or_nothing(project) -> None:
    root, journal = project
    path = root / "src" / "pkg" / "util.ts"
    before = path.read_text(encoding="utf-8")
    with pytest.raises(ValueError, match="edit 2"):
        wc.workspace_multi_edit("src/pkg/util.ts", edits=[{"old_text": "x + 1", "new_text": "x + 2"}, {"old_text": "nope", "new_text": "y"}])
    assert path.read_text(encoding="utf-8") == before and not journal.records
    out = _call(wc.workspace_multi_edit, "src/pkg/util.ts", edits=[
        {"old_text": "x + 1", "new_text": "x + 2"},
        {"old_text": "x + 2", "new_text": "x + 3"},
        {"old_text": "a: number", "new_text": "a: string"},
    ])
    text = path.read_text(encoding="utf-8")
    assert "x + 3" in text and "a: string" in text
    assert out["edits"] == 3 and len(journal.records) == 1


def test_replace_lines_replaces_inserts_and_deletes(project) -> None:
    root, _ = project
    path = root / "README.md"
    _call(wc.workspace_replace_lines, "README.md", start_line=3, end_line=3, new_text="Better intro")
    assert path.read_text(encoding="utf-8") == "# Title\n\nBetter intro\n\n## Setup\n\nRun it.\n"
    _call(wc.workspace_replace_lines, "README.md", start_line=1, end_line=0, new_text="<!-- top -->")
    assert path.read_text(encoding="utf-8").startswith("<!-- top -->\n# Title\n")
    _call(wc.workspace_replace_lines, "README.md", start_line=2, end_line=3, new_text="")
    assert path.read_text(encoding="utf-8").startswith("<!-- top -->\nBetter intro\n")
    with pytest.raises(ValueError, match="outside the file"):
        wc.workspace_replace_lines("README.md", start_line=40, end_line=41, new_text="x")


def test_replace_lines_keeps_crlf(project) -> None:
    root, _ = project
    path = root / "win.txt"
    path.write_bytes(b"a\r\nb\r\nc\r\n")
    _call(wc.workspace_replace_lines, "win.txt", start_line=2, end_line=2, new_text="B")
    assert path.read_bytes() == b"a\r\nB\r\nc\r\n"


def test_edit_matches_crlf_files_read_back_as_lf(project) -> None:
    root, _ = project
    path = root / "win.txt"
    path.write_bytes(b"one\r\ntwo\r\nthree\r\n")
    _call(wc.workspace_edit_file, "win.txt", old_text="one\ntwo", new_text="uno\ndos")
    assert path.read_bytes() == b"uno\r\ndos\r\nthree\r\n"


def _stale_test_edit(kind, path, replacement):
    if kind == "edit":
        return wc.workspace_edit_file(path, old_text="baseline", new_text=replacement)
    if kind == "multi_edit":
        return wc.workspace_multi_edit(path, edits=[{"old_text": "baseline", "new_text": replacement},
                                               {"old_text": "tail", "new_text": "changed tail"}])
    return wc.workspace_replace_lines(path, start_line=1, end_line=1, new_text=replacement)


@pytest.mark.parametrize("kind", ["edit", "multi_edit", "replace_lines"])
@pytest.mark.parametrize("alias", ["src/./shared.txt", "src\\shared.txt"])
def test_same_baseline_concurrent_edits_admit_exactly_one(project, monkeypatch, kind, alias):
    from concurrent.futures import ThreadPoolExecutor
    import threading
    from backend.workspace.writer import StaleWrite

    root, journal = project
    path = root / "src/shared.txt"
    path.write_text("baseline\ntail\n", encoding="utf-8")
    barrier = threading.Barrier(2)
    original_read = wc._current_text
    def same_baseline(rel):
        text = original_read(rel)
        barrier.wait(timeout=5)
        return text
    monkeypatch.setattr(wc, "_current_text", same_baseline)
    def edit(item):
        rel, text = item
        try:
            _stale_test_edit(kind, rel, text)
            return text, None
        except StaleWrite as exc:
            return text, exc
    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(edit, [("src/shared.txt", "winner A"), (alias, "winner B")]))
    successes = [text for text, error in outcomes if error is None]
    failures = [error for _, error in outcomes if error is not None]
    assert len(successes) == len(failures) == 1
    assert "changed" in str(failures[0]).lower()
    assert path.read_text(encoding="utf-8").splitlines()[0] == successes[0]
    assert len(journal.records) == 1
    assert journal.records[0].before == "baseline\ntail\n"
    assert journal.records[0].after.encode("utf-8") == path.read_bytes()
    assert not list(path.parent.glob(".shared.txt.*.tmp"))


@pytest.mark.parametrize("kind", ["edit", "multi_edit", "replace_lines"])
@pytest.mark.parametrize("newline", [b"\n", b"\r\n"])
def test_intervening_human_edit_survives_stale_tool_update(project, monkeypatch, kind, newline):
    from unittest.mock import Mock
    from backend.workspace.writer import StaleWrite

    root, journal = project
    path = root / "src/shared.txt"
    path.write_bytes(newline.join([b"baseline", b"tail", b""]))
    human = newline.join([b"human change", b"tail", b""])
    original_read = wc._current_text
    def human_changes_after_read(rel):
        before = original_read(rel)
        path.write_bytes(human)
        return before
    monkeypatch.setattr(wc, "_current_text", human_changes_after_read)
    observer = Mock()
    runtime.get_writer().add_observer(observer)
    with pytest.raises(StaleWrite):
        _stale_test_edit(kind, "src/shared.txt", "agent replacement")
    assert path.read_bytes() == human
    assert journal.records == []
    observer.on_write.assert_not_called()
    assert not list(path.parent.glob(".shared.txt.*.tmp"))


def test_move_and_delete_use_the_undoable_project_operations(project, monkeypatch) -> None:
    from frontend.ui_web import project_files

    calls: list[tuple] = []
    monkeypatch.setattr(project_files, "move_project_entry", lambda src, parent: calls.append(("move", src, parent)) or {"path": f"{parent}/{src.rsplit('/', 1)[-1]}"})
    monkeypatch.setattr(project_files, "rename_project_entry", lambda src, name: calls.append(("rename", src, name)) or {"path": f"{src.rsplit('/', 1)[0]}/{name}"})
    monkeypatch.setattr(project_files, "delete_project_entry", lambda rel: calls.append(("delete", rel)) or {"path": rel})
    assert _call(wc.workspace_move_file, "src/app.py", "lib/main.py")["path"] == "lib/main.py"
    assert calls == [("move", "src/app.py", "lib"), ("rename", "lib/app.py", "main.py")]
    calls.clear()
    _call(wc.workspace_move_file, "src/app.py", "src/core.py")
    assert calls == [("rename", "src/app.py", "core.py")]
    out = _call(wc.workspace_delete_file, "README.md")
    assert out == {"path": "README.md", "deleted": True, "restorable": True}


def test_reads_reach_another_of_the_persons_projects_by_absolute_path(project, monkeypatch, tmp_path) -> None:
    root, _ = project
    other = tmp_path / "OtherRepo"
    (other / "lib").mkdir(parents=True)
    (other / "lib" / "media.py").write_text("AGENT_BACKEND = 'agent'\n\ndef pick():\n    return AGENT_BACKEND\n", encoding="utf-8")
    inner = wc.resolve_workspace_path

    def resolve(path: str) -> str:
        # The real resolver accepts absolute paths inside any recent project.
        if Path(path).is_absolute():
            full = Path(path).resolve()
            assert other in full.parents or full == other, "outside the person's projects"
            return str(full)
        return inner(path)

    monkeypatch.setattr(wc, "resolve_workspace_path", resolve)
    monkeypatch.setattr(system, "resolve_workspace_path", resolve)
    want = str(other / "lib" / "media.py").replace("\\", "/")
    hits = _call(wc.workspace_search, "AGENT_BACKEND", path=str(other), glob="lib/*.py")
    assert {(m["path"], m["line"]) for m in hits["matches"]} == {(want, 1), (want, 4)}
    assert _call(wc.workspace_find, "lib/*.py", path=str(other))["files"] == [want]
    read = _call(wc.workspace_read_files, [want], start_line=3, end_line=4)["files"][0]
    assert read["path"] == want and read["content"] == "def pick():\n    return AGENT_BACKEND"
    # The open project's own paths stay relative.
    assert _call(wc.workspace_find, "*.ts")["files"] == ["src/pkg/util.ts"]


# ---------------------------------------------------------------- git


@pytest.mark.parametrize(
    "command,args",
    [
        ("commit", ["-m", "x"]),
        ("push", []),
        ("checkout", ["main"]),
        ("diff", ["--output=C:/x.txt"]),
        ("diff", ["--out=x"]),
        ("diff", ["--no-index", "a", "b"]),
        ("blame", ["--contents", "C:/secret", "a.py"]),
        ("grep", ["-Ovim", "x"]),
        ("grep", ["--open-files", "x"]),
        ("show", ["--ext-diff"]),
        ("branch", ["new-branch"]),
        ("branch", ["-D", "main"]),
        ("tag", ["v1"]),
    ],
)
def test_git_refuses_anything_that_changes_or_escapes(project, command, args) -> None:
    with pytest.raises(ValueError):
        wc.workspace_git(command, args)


@pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")
def test_git_reads_status_log_and_branches(project) -> None:
    from frontend.settings import PanelSettings
    settings = PanelSettings.load()
    settings.ai_ignore_strict = False
    settings.save()
    root, _ = project
    for argv in (["init", "-q"], ["config", "user.email", "t@example.com"], ["config", "user.name", "t"],
                 ["add", "src/app.py"], ["commit", "-q", "-m", "first"]):
        subprocess.run(["git", *argv], cwd=root, check=True, capture_output=True)
    (root / "src" / "app.py").write_text("changed\n", encoding="utf-8")
    status = _call(wc.workspace_git, "status", ["--short"])
    assert status["exit_code"] == 0 and "M src/app.py" in status["output"]
    assert "first" in _call(wc.workspace_git, "log", ["--oneline", "-5"])["output"]
    assert "-import os" in _call(wc.workspace_git, "diff", ["--", "src/app.py"])["output"]
    assert _call(wc.workspace_git, "branch", ["--show-current"])["exit_code"] == 0
