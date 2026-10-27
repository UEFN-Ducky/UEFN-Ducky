"""Read, search, edit and git tools that replace shell commands for coding agents."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import threading
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
    out = _call(system.workspace_read_file, "src/app.py", start_line=4, end_line=6, line_numbers=True, format="json")
    assert out["content"] == "4| class Store:\n5|     def load(self):\n6|         return 1"
    assert out["total_lines"] == 13 and (out["start_line"], out["end_line"]) == (4, 6)
    whole = _call(system.workspace_read_file, "src/app.py", format="json")
    assert whole["content"].startswith("import os") and "start_line" not in whole and "more" not in whole


def test_read_without_range_stops_at_the_cap(project, monkeypatch) -> None:
    root, _ = project
    (root / "big.txt").write_text("".join(f"line {n}\n" for n in range(1, 11)), encoding="utf-8")
    monkeypatch.setattr(wc, "READ_LINE_CAP", 4)
    out = _call(system.workspace_read_file, "big.txt", format="json")
    assert out["content"].splitlines() == ["line 1", "line 2", "line 3", "line 4"]
    assert "start_line=5" in out["more"]


def test_one_read_never_floods_the_model(project, monkeypatch) -> None:
    root, _ = project
    monkeypatch.setattr(wc, "READ_CHAR_CAP", 25)
    (root / "lines.txt").write_text("".join(f"line {n}\n" for n in range(1, 11)), encoding="utf-8")
    out = _call(system.workspace_read_file, "lines.txt", format="json")
    assert out["content"].splitlines() == ["line 1", "line 2", "line 3"]
    assert out["end_line"] == 3 and "start_line=4" in out["more"]
    (root / "min.js").write_text("x" * 100 + "\nnext\n", encoding="utf-8")
    one = _call(system.workspace_read_file, "min.js", format="json")
    assert one["content"] == "x" * 25 and "Line 1 is longer" in one["more"] and "start_line=2" in one["more"]


def test_reads_a_file_attached_to_a_chat(project, tmp_path, monkeypatch) -> None:
    import frontend.settings as settings

    monkeypatch.setattr(settings, "default_app_data_dir", lambda: tmp_path / "appdata")
    att = tmp_path / "appdata" / "chats" / "projects" / "p" / "conversations" / "c1" / "attachments"
    att.mkdir(parents=True)
    kit = att / "1_0_Kit.html.readable.txt"
    kit.write_text("a\nb\nc\n", encoding="utf-8")
    out = _call(system.workspace_read_file, path=str(kit), start_line=2, end_line=3, format="json")
    assert out["content"] == "b\nc"
    outside = tmp_path / "elsewhere.txt"
    outside.write_text("secret", encoding="utf-8")
    assert wc.chat_file_path(str(outside)) == ""


def test_read_files_reads_several_and_reports_missing(project) -> None:
    out = _call(wc.workspace_read_files, ["src/app.py", "nope.py", "README.md"], start_line=1, end_line=1,
                format="json")
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
    out = _call(wc.workspace_search, "store", format="json")
    assert {(m["path"], m["line"]) for m in out["matches"]} == {("src/app.py", 4), ("src/app.py", 13)}
    assert out["total_hits"] == 2 and out["files_with_hits"] == 1


def test_search_modes_glob_regex_and_context(project) -> None:
    files = _call(wc.workspace_search, "return", output_mode="files", format="json")["files"]
    assert {f["path"] for f in files} == {"src/app.py", "src/pkg/util.ts"}
    only_ts = _call(wc.workspace_search, "return", glob="*.ts", format="json")
    assert [m["path"] for m in only_ts["matches"]] == ["src/pkg/util.ts"]
    hit = _call(wc.workspace_search, r"def \w+\(self\)", regex=True, context=1, case_sensitive=True,
                format="json")["matches"][0]
    assert hit["line"] == 5 and hit["before"] == ["class Store:"] and hit["after"] == ["        return 1"]
    assert _call(wc.workspace_search, "return", output_mode="count", format="json")["total_hits"] == 3
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
    # .verse entries say whether UEFN's Verse build was told (Epic VerseToolset) or not.
    monkeypatch.setattr(project_files, "delete_project_entry", lambda rel: {"path": rel, "via": "uefn"})
    assert _call(wc.workspace_delete_file, "Verse/A")["via"] == "uefn"
    monkeypatch.setattr(project_files, "move_project_entry", lambda src, parent: {"path": f"{parent}/A", "via": "uefn"})
    monkeypatch.setattr(project_files, "rename_project_entry", lambda src, name: {"path": f"lib/{name}", "via": "disk"})
    assert _call(wc.workspace_move_file, "src/A", "lib/B")["via"] == "disk"


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
    hits = _call(wc.workspace_search, "AGENT_BACKEND", path=str(other), glob="lib/*.py", format="json")
    assert {(m["path"], m["line"]) for m in hits["matches"]} == {(want, 1), (want, 4)}
    assert _call(wc.workspace_find, "lib/*.py", path=str(other))["files"] == [want]
    read = _call(wc.workspace_read_files, [want], start_line=3, end_line=4, format="json")["files"][0]
    assert read["path"] == want and read["content"] == "def pick():\n    return AGENT_BACKEND"
    # The open project's own paths stay relative.
    assert _call(wc.workspace_find, "*.ts")["files"] == ["src/pkg/util.ts"]
    # Text output outside the project names the folder once instead of on every line.
    text = wc.workspace_search("AGENT_BACKEND", path=str(other), output_mode="files")
    first, *rows = text.splitlines()
    assert first.endswith(f"Paths are relative to {str(other).replace(chr(92), '/')}/")
    assert rows == ["lib/media.py  2"]
    content = wc.workspace_search("pick", path=str(other))
    assert content.splitlines()[1] == "lib/media.py:3: def pick():"
    # Inside the project nothing changes.
    assert "relative to" not in wc.workspace_search("Store", output_mode="files")


def test_big_multi_file_reads_spill_and_page_back(project, monkeypatch, tmp_path) -> None:
    from backend.agent import tool_spills

    root, _ = project
    monkeypatch.setattr(tool_spills, "tool_spills_dir", lambda for_write=False: _mkdir(tmp_path / "spills"))
    for i in range(3):
        (root / f"big{i}.txt").write_text(("x" * 99 + "\n") * 150, encoding="utf-8")
    small = wc.workspace_read_files(["big0.txt"])
    assert small.startswith("big0.txt  lines 1-150 of 150")
    stub = json.loads(wc.workspace_read_files(["big0.txt", "big1.txt", "big2.txt"]))
    assert stub["truncated"] is True and stub["chars"] > wc.INLINE_RESULT_CHARS
    page = tool_spills.read_tool_spill(stub["name"], 0, 200)
    assert page["chunk"].startswith("big0.txt  lines 1-150 of 150") and page["truncated"]


def _mkdir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


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
    status = _call(wc.workspace_git, "status", ["--short"], format="json")
    assert status["exit_code"] == 0 and "M src/app.py" in status["output"]
    # Plain text by default: git's own output, an exit header only on failure.
    assert wc.workspace_git("status", ["--short"]).startswith(" M src/app.py")
    assert "first" in wc.workspace_git("log", ["--oneline", "-5"])
    assert "-import os" in wc.workspace_git("diff", ["--", "src/app.py"])
    assert not wc.workspace_git("branch", ["--show-current"]).startswith("exit ")
    failed = wc.workspace_git("show", ["no-such-ref"])
    assert failed.startswith("exit 128\n") and "no-such-ref" in failed


def test_multi_edit_takes_claudes_old_string_new_string(project) -> None:
    # Oct 22 2026: a Sonnet 5.5 member wrote its edits the way Claude's own Edit tool takes them
    # and the whole multi-edit failed with "old_text is required".
    root, _ = project
    path = root / "src" / "pkg" / "util.ts"
    out = _call(wc.workspace_multi_edit, "src/pkg/util.ts", edits=[
        {"old_string": "x + 1", "new_string": "x + 5"}, {"old_text": "a: number", "new_text": "a: bigint"}])
    text = path.read_text(encoding="utf-8")
    assert "x + 5" in text and "a: bigint" in text and out["replacements"] == 2


def test_listing_a_folder_that_is_not_there_yet_is_an_answer(project) -> None:
    # Agents list the folder they are about to write into; the error cost a turn.
    root, _ = project
    out = _call(system.workspace_list_dir, "Content/Verse/DailyStreak")
    assert out["exists"] is False and out["entries"] == []
    with pytest.raises(ValueError, match="is a file"):
        system.workspace_list_dir("README.md")
    assert "util.ts" in _call(system.workspace_list_dir, "src/pkg")["entries"]


# ---------------------------------------------------------------- git-aware search / find


def _git(root: Path, *argv: str) -> None:
    subprocess.run(["git", *argv], cwd=root, check=True, capture_output=True)


@pytest.fixture
def git_project(project):
    if shutil.which("git") is None:
        pytest.skip("git not installed")
    from frontend.settings import PanelSettings
    settings = PanelSettings.load()
    settings.ai_ignore_strict = False
    settings.save()
    root, journal = project
    (root / ".gitignore").write_text("ignored/\n*.log\n", encoding="utf-8")
    (root / "ignored").mkdir()
    (root / "ignored" / "skip.py").write_text("needle = 'ignored'\n", encoding="utf-8")
    (root / "debug.log").write_text("needle in a log\n", encoding="utf-8")
    (root / "src" / "app.py").write_text("needle = 1\n\ndef find():\n    return needle\n", encoding="utf-8")
    for argv in (["init", "-q"], ["config", "user.email", "t@example.com"], ["config", "user.name", "t"],
                 ["add", "src/app.py", ".gitignore"], ["commit", "-q", "-m", "first"]):
        _git(root, *argv)
    # Untracked but not ignored: git grep --untracked still finds it.
    (root / "src" / "pkg" / "new.ts").write_text("export const needle = 2;\n", encoding="utf-8")
    yield root, journal


def test_search_in_a_git_repo_uses_git_grep(git_project, monkeypatch) -> None:
    def no_walk(*_a, **_k):
        raise AssertionError("searched by walking instead of git grep")

    monkeypatch.setattr(wc, "_walk_search", no_walk)
    out = _call(wc.workspace_search, "needle", format="json")
    assert out["complete"] is True and list(out)[0] == "complete"
    assert "files_searched" not in out  # git grep, not the walk
    assert {(m["path"], m["line"]) for m in out["matches"]} == {
        ("src/app.py", 1), ("src/app.py", 4), ("src/pkg/new.ts", 1)}
    only_ts = _call(wc.workspace_search, "needle", glob="src/**/*.ts", format="json")
    assert [m["path"] for m in only_ts["matches"]] == ["src/pkg/new.ts"]
    hit = _call(wc.workspace_search, r"def \w+\(\)", regex=True, context=1, format="json")["matches"]
    assert hit == [{"path": "src/app.py", "line": 3, "text": "def find():", "before": [""],
                    "after": ["    return needle"]}]
    assert _call(wc.workspace_search, "NEEDLE", case_sensitive=True, format="json")["matches"] == []
    files = _call(wc.workspace_search, "needle", output_mode="files", format="json")
    assert {f["path"]: f["hits"] for f in files["files"]} == {"src/app.py": 2, "src/pkg/new.ts": 1}
    # A folder inside the repo is searched by git too, paths stay project-relative.
    sub = _call(wc.workspace_search, "needle", path="src/pkg", format="json")
    assert [m["path"] for m in sub["matches"]] == ["src/pkg/new.ts"]


def test_git_search_text_lists_hits_and_context(git_project) -> None:
    text = wc.workspace_search("return needle", context=1)
    assert text.splitlines() == [
        "complete: 1 match in 1 file",
        "src/app.py-3- def find():",
        "src/app.py:4:     return needle",
    ]
    assert wc.workspace_search("nothing like this").startswith("complete: no matches")
    assert wc.workspace_search("needle", output_mode="files").splitlines()[1:] == ["src/app.py  2", "src/pkg/new.ts  1"]


def test_git_search_stops_loudly_at_the_limit(git_project) -> None:
    out = _call(wc.workspace_search, "needle", max_results=2, format="json")
    assert out["complete"] is False and out["note"].startswith("INCOMPLETE: stopped at 2 matches")
    assert len(out["matches"]) == 2
    # Exactly the limit is not cut short.
    assert _call(wc.workspace_search, "needle", max_results=3, format="json")["complete"] is True


def test_git_search_at_the_limit_returns_through_git_for_windows_launcher(git_project, monkeypatch) -> None:
    # cmd\git.exe starts the real git.exe as a child; killing only the launcher left the
    # child holding stdout/stderr and the search hung forever (agents stuck in search).
    launcher = Path(r"C:\Program Files\Git\cmd\git.exe")
    if not launcher.is_file():
        pytest.skip("Git for Windows launcher not installed")
    monkeypatch.setenv("PATH", f"{launcher.parent};{os.environ.get('PATH', '')}")
    root, _ = git_project
    for i in range(200):
        (root / "src" / f"many{i}.py").write_text("needle = 1\n" * 5, encoding="utf-8")
    box: list[dict] = []
    t = threading.Thread(target=lambda: box.append(_call(wc.workspace_search, "needle", max_results=20, format="json")),
                         daemon=True)
    t.start()
    t.join(15)
    assert box, "workspace_search hung at its limit"
    assert box[0]["complete"] is False and len(box[0]["matches"]) == 20


def test_git_search_keeps_ai_ignore_rules(git_project, monkeypatch) -> None:
    from backend.workspace import ai_ignore
    root, _ = git_project
    real = ai_ignore.IgnorePolicy.denied
    monkeypatch.setattr(ai_ignore.IgnorePolicy, "denied",
                        lambda self, p: str(p).replace("\\", "/").endswith("new.ts") or real(self, p))
    out = _call(wc.workspace_search, "needle", format="json")
    assert {m["path"] for m in out["matches"]} == {"src/app.py"}
    assert "src/pkg/new.ts" not in _call(wc.workspace_find, "*.ts")["files"]


def test_ignored_folder_given_explicitly_is_walked(git_project) -> None:
    out = _call(wc.workspace_search, "needle", path="ignored", format="json")
    assert [m["path"] for m in out["matches"]] == ["ignored/skip.py"] and "files_searched" in out


def test_find_in_a_git_repo_uses_ls_files(git_project, monkeypatch) -> None:
    def no_walk(*_a, **_k):
        raise AssertionError("listed by walking instead of git ls-files")

    monkeypatch.setattr(wc, "_walk_files", no_walk)
    out = _call(wc.workspace_find, "*.py")
    assert out["complete"] is True and out["files"] == ["src/app.py"]  # ignored/skip.py stays out
    assert _call(wc.workspace_find, "*.ts")["files"] == ["src/pkg/new.ts", "src/pkg/util.ts"]
    assert _call(wc.workspace_find, "*.log")["files"] == []
    capped = _call(wc.workspace_find, "src/**", max_results=1)
    assert capped["complete"] is False and capped["note"].startswith("INCOMPLETE: stopped at 1 files")


def test_walk_prunes_by_glob_and_gitignore(project, monkeypatch) -> None:
    root, _ = project
    (root / "other").mkdir()
    (root / "other" / "x.ts").write_text("needle\n", encoding="utf-8")
    (root / "src" / "pkg" / "b.ts").write_text("needle\n", encoding="utf-8")
    (root / "gen").mkdir()
    (root / "gen" / "c.ts").write_text("needle\n", encoding="utf-8")
    (root / "src" / "pkg" / "out.ts").write_text("needle\n", encoding="utf-8")
    (root / ".gitignore").write_text("# build output\n/gen/\nout.ts\n", encoding="utf-8")
    listed, read = [], []
    real_scandir, real_read = wc.os.scandir, wc._read_text
    monkeypatch.setattr(wc.os, "scandir", lambda p: listed.append(Path(p)) or real_scandir(p))
    monkeypatch.setattr(wc, "_read_text", lambda p, **k: read.append(Path(p)) or real_read(p, **k))
    out = _call(wc.workspace_search, "needle", glob="src/**/*.ts", format="json")
    assert [m["path"] for m in out["matches"]] == ["src/pkg/b.ts"]
    assert all(root / "src" in p.parents for p in read), read
    assert root / "other" not in listed and root / "node_modules" not in listed
    # .gitignore at the root hides /gen/ and out.ts from the walk too.
    everywhere = _call(wc.workspace_search, "needle", format="json")
    assert {m["path"] for m in everywhere["matches"]} == {"other/x.ts", "src/pkg/b.ts"}
    assert _call(wc.workspace_find, "*.ts")["files"] == ["other/x.ts", "src/pkg/b.ts", "src/pkg/util.ts"]


def test_glob_star_stays_inside_one_folder() -> None:
    assert wc._path_matches("lib/a.py", "lib/*.py")
    assert not wc._path_matches("lib/sub/a.py", "lib/*.py")
    assert wc._path_matches("src/a.py", "src/**/*.py") and wc._path_matches("src/x/y/a.py", "src/**/*.py")
    assert wc._path_matches("a/b/c.ts", "**/*.ts") and wc._path_matches("c.ts", "**/*.ts")
    assert wc._folder_may_hold("src/pkg", ["src/**/*.ts"]) and not wc._folder_may_hold("other", ["src/**/*.ts"])
    assert not wc._folder_may_hold("lib/sub", ["lib/*.py"]) and wc._folder_may_hold("anything", ["*.py"])


def test_search_that_runs_out_of_time_says_incomplete(project, monkeypatch) -> None:
    monkeypatch.setattr(wc, "_SEARCH_SECONDS", -1.0)
    out = _call(wc.workspace_search, "Store", format="json")
    assert list(out)[:2] == ["complete", "note"]
    assert out["complete"] is False and out["matches"] == []
    assert out["note"].startswith("INCOMPLETE: time limit -1 s, searched 0 files") and "narrow path or pattern" in out["note"]
    assert out["truncated"] is True and out["searched_files"] == 0 and out["hint"] == "narrow path or pattern"
    assert wc.workspace_search("Store").startswith("INCOMPLETE: time limit -1 s, searched 0 files")
    found = _call(wc.workspace_find, "*.py")
    assert found["complete"] is False and found["note"].startswith("INCOMPLETE:") and found["truncated"] is True


def _slow_folders(root: Path, monkeypatch, delay: float = 0.05) -> None:
    """Forty folders of files no glob matches, each listed slowly."""
    for i in range(40):
        (root / "deep" / f"d{i:02}").mkdir(parents=True)
        (root / "deep" / f"d{i:02}" / "a.txt").write_text("Store\n", encoding="utf-8")
    real_scandir = wc.os.scandir

    def slow(path):
        import time
        time.sleep(delay)
        return real_scandir(path)

    monkeypatch.setattr(wc.os, "scandir", slow)


def test_a_slow_walk_returns_at_the_time_limit_with_what_it_found(project, monkeypatch) -> None:
    """Searches ran past 120 s (Oct 24 2026): folders whose files no glob matched yielded
    nothing, so the clock check between yielded files never ran."""
    import time

    root, _ = project
    _slow_folders(root, monkeypatch)
    monkeypatch.setattr(wc, "_SEARCH_SECONDS", 0.5)
    started = time.monotonic()
    out = _call(wc.workspace_search, "Store", glob="*.zz", format="json")
    assert time.monotonic() - started < 1.5
    assert out["truncated"] is True and out["reason"] == "time limit 0 s" and out["complete"] is False
    started = time.monotonic()
    hits = _call(wc.workspace_search, "Store", format="json")
    assert time.monotonic() - started < 1.5
    assert hits["truncated"] is True and hits["matches"] and hits["searched_files"] >= 1
    started = time.monotonic()
    found = _call(wc.workspace_find, "*.zz")
    assert time.monotonic() - started < 1.5
    assert found["truncated"] is True and found["complete"] is False


def test_a_small_tree_searches_everything_within_the_limit(project, monkeypatch) -> None:
    root, _ = project
    _slow_folders(root, monkeypatch, delay=0.0)
    out = _call(wc.workspace_search, "Store", format="json")
    assert out["complete"] is True and "truncated" not in out
    assert {m["path"] for m in out["matches"]} >= {"src/app.py", "deep/d00/a.txt", "deep/d39/a.txt"}
    assert not any(m["path"].startswith("node_modules/") for m in out["matches"])
    found = _call(wc.workspace_find, "*.txt")
    assert found["complete"] is True and found["count"] == 40 and "truncated" not in found
    assert _call(wc.workspace_find, "*.js", path="node_modules")["files"] == ["node_modules/dep/index.js"]


def test_search_limit_in_a_plain_folder_is_loud(project) -> None:
    out = _call(wc.workspace_search, "return", max_results=1, format="json")
    assert out["complete"] is False and "stopped at 1 matches" in out["note"] and len(out["matches"]) == 1
    assert _call(wc.workspace_search, "store", format="json")["complete"] is True


REPO = Path(__file__).resolve().parents[4]


@pytest.mark.skipif(shutil.which("git") is None or not (REPO / ".git").exists(), reason="needs this git checkout")
def test_search_and_find_on_this_repo_are_fast(monkeypatch) -> None:
    import time

    def resolve(rel: str) -> str:
        rel = (rel or ".").replace("\\", "/").strip("/")
        return str(REPO if rel in ("", ".") else REPO / rel)

    monkeypatch.setattr(wc, "resolve_workspace_path", resolve)
    began = time.monotonic()
    out = json.loads(wc.workspace_search("settings_changed", output_mode="files", format="json"))
    took = time.monotonic() - began
    assert out["complete"] is True
    assert any(f["path"].endswith("web/src/types/panel.ts") for f in out["files"]), out
    began = time.monotonic()
    found = json.loads(wc.workspace_find("*.verse", max_results=2000))
    took_find = time.monotonic() - began
    assert found["complete"] is True and found["count"] > 0
    # 1 s on a dev PC; the margin is for slow CI runners.
    assert took < 3 and took_find < 3, (took, took_find)


# ---------------------------------------------------------------- plain text


def test_read_files_returns_plain_text_with_a_header(project) -> None:
    text = wc.workspace_read_files(["src/app.py", "nope.py"], start_line=4, end_line=5, line_numbers=True)
    assert text.splitlines() == [
        "src/app.py  lines 4-5 of 13",
        "4| class Store:",
        "5|     def load(self):",
        "",
        "nope.py  ERROR: Not a file: nope.py. Find it with workspace_find.",
    ]
    whole = wc.workspace_read_files(["README.md"])
    assert whole == "README.md  lines 1-7 of 7\n# Title\n\nIntro\n\n## Setup\n\nRun it."


def test_plain_text_read_is_much_smaller_than_json(project, monkeypatch) -> None:
    # What a client receives: FastMCP wraps a str result as {"result": "<the string>"}.
    monkeypatch.setattr(wc, "resolve_workspace_path", lambda rel: str(Path(wc.__file__)))
    real_json = json.dumps
    as_json = wc.workspace_read_files(["workspace_code.py"], start_line=1, end_line=300, format="json")
    as_text = wc.workspace_read_files(["workspace_code.py"], start_line=1, end_line=300)
    wire_json, wire_text = len(real_json({"result": as_json})), len(real_json({"result": as_text}))
    # Still wrapped: no JSON escaping inside the string (about 8% on this file). Sent as the
    # text itself, without the wrapper: about 13%.
    assert wire_text <= 0.95 * wire_json and len(as_text) <= 0.9 * wire_json, (wire_text, len(as_text), wire_json)


def test_format_read_marks_what_is_not_shown() -> None:
    part = {"content": "a\nb", "total_lines": 9, "start_line": 1, "end_line": 2,
            "more": "Lines 3-9 not shown: read them with start_line=3."}
    assert wc.format_read("x.py", part) == "x.py  lines 1-2 of 9\na\nb\n[Lines 3-9 not shown: read them with start_line=3.]"
    assert wc.format_read("e.txt", {"content": "", "total_lines": 0}) == "e.txt  empty file\n"


# ---------------------------------------------------------------- descriptions


WORKSPACE_TOOLS = (
    "workspace_read_files", "workspace_file_outline", "workspace_tree", "workspace_search", "workspace_find",
    "workspace_edit_file", "workspace_multi_edit", "workspace_replace_lines", "workspace_move_file",
    "workspace_delete_file", "workspace_git",
)


@pytest.mark.parametrize("name", WORKSPACE_TOOLS)
def test_first_description_line_says_what_for_and_never(name) -> None:
    import inspect

    first = inspect.getdoc(getattr(wc, name)).splitlines()[0]
    assert len(first) < 120, first
    assert "never" in first.lower(), first


def test_view_image_opens_the_capture_file_a_chat_names(tmp_path: Path, monkeypatch):
    captures = tmp_path / "tool_captures"
    captures.mkdir()
    shot = captures / "Screenshot 2026-10-25 131252.png"
    shot.write_bytes(b"\x89PNG\r\n")
    monkeypatch.setattr("frontend.ui_web.tool_captures.tool_captures_dir", lambda **_: captures)
    assert wc._image_path(str(shot)) == os.path.realpath(shot)
    # Only straight inside the folder: a nested or outside picture still goes through the workspace check.
    assert wc._capture_file(str(tmp_path / "elsewhere.png")) == ""
