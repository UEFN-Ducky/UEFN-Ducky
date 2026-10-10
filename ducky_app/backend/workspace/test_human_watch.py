"""External file edits become a You row; pipeline writes do not double-count."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from backend.workspace import human_watch as hw
from backend.workspace import identity, runtime
from backend.workspace.human_watch import HumanWatch
from backend.workspace.identity import RunContext
from backend.workspace.journal import FileChangeJournal
from backend.workspace.writer import ProjectWriter


@pytest.fixture
def env(tmp_path: Path, monkeypatch):
    from frontend.ui_web import live_agent_runs

    root = tmp_path / "Proj"
    verse = root / "Content" / "Verse"
    verse.mkdir(parents=True)
    (verse / "a.verse").write_text("orig\n", encoding="utf-8", newline="\n")
    journal = FileChangeJournal(lambda _r: tmp_path / "store")
    writer = ProjectWriter.for_root(str(root), journal=journal)
    runtime.reset_for_tests(writer)
    hw.reset_for_tests()
    live_agent_runs.reset_for_tests()
    monkeypatch.setattr(hw, "STABLE_POLLS", 2)
    yield root, journal, writer, verse
    runtime.reset_for_tests(None)
    hw.reset_for_tests()
    live_agent_runs.reset_for_tests()


def _flush(watch: HumanWatch, n: int = 2) -> None:
    for _ in range(n):
        watch.tick()


def test_external_edit_and_delete_become_you_rows(env, monkeypatch) -> None:
    root, journal, writer, verse = env
    for key in RunContext().to_env():
        monkeypatch.delenv(key, raising=False)
    watch = HumanWatch()
    watch.tick()  # seed, no rows
    assert journal.list_runs(project_root=str(root)) == []

    (verse / "a.verse").write_text("edited\n", encoding="utf-8", newline="\n")
    _flush(watch)
    runs = journal.list_runs(project_root=str(root))
    assert len(runs) == 1
    assert runs[0]["source"] == "user" and runs[0]["ducky_name"] == "You"
    [edit] = [e for e in runs[0]["entries"] if e["op"] == "write"]
    assert edit["tool"] == "external" and edit["before_hash"]
    body = journal.entry_contents(runs[0]["run_id"], edit["seq"], project_root=str(root))
    assert body["before"] == "orig\n" and body["after"] == "edited\n"

    (verse / "a.verse").unlink()
    _flush(watch)
    run = journal.get_run(runs[0]["run_id"], project_root=str(root))
    deletes = [e for e in run["entries"] if e["op"] == "delete"]
    assert len(deletes) == 1
    gone = journal.entry_contents(run["run_id"], deletes[0]["seq"], project_root=str(root))
    assert gone["before"] == "edited\n"


def test_pipeline_write_is_not_double_counted(env) -> None:
    root, journal, writer, verse = env
    watch = HumanWatch()
    watch.tick()
    token = identity.bind(RunContext(run_id="r1", conv_id="c1", ducky_name="Hacker"))
    try:
        writer.write_text("Content/Verse/a.verse", "agent\n")
    finally:
        identity.reset(token)
    _flush(watch)
    runs = journal.list_runs(project_root=str(root))
    assert [r["run_id"] for r in runs] == ["r1"]
    assert all(e["tool"] != "external" for e in runs[0]["entries"])


def test_content_stripped_keys_do_not_become_you_rows(env) -> None:
    """Old watcher keys were Verse/x when the writer root was Content/."""
    from backend.workspace.paths import content_hash

    root, journal, writer, verse = env
    watch = HumanWatch()
    watch.tick()
    watch._seen = {  # noqa: SLF001
        "Verse/a.verse": {
            "hash": content_hash("orig\n"),
            "content": "orig\n",
            "mtime_ns": 1,
            "size": 5,
            "full": str(verse / "a.verse"),
        }
    }
    _flush(watch)
    assert journal.list_runs(project_root=str(root)) == []


def test_ducky_plan_files_are_not_you_rows(env) -> None:
    root, journal, writer, verse = env
    ducky = root / ".ducky" / "plans"
    ducky.mkdir(parents=True)
    watch = HumanWatch()
    watch.tick()
    (ducky / "plan.json").write_text('{"n":1}\n', encoding="utf-8")
    _flush(watch)
    assert journal.list_runs(project_root=str(root)) == []


def test_live_agent_disk_edit_is_not_a_you_row(env) -> None:
    from frontend.ui_web import live_agent_runs

    root, journal, writer, verse = env
    live_agent_runs.set_live_writer(
        "ld-1",
        {
            "source": "agent",
            "run_id": "ld-1",
            "conv_id": "c-ld",
            "ducky_name": "Level Designer",
            "coding_agent": "claude_code",
        },
    )
    watch = HumanWatch()
    watch.tick()
    (verse / "a.verse").write_text("agent edit\n", encoding="utf-8", newline="\n")
    _flush(watch)
    runs = journal.list_runs(project_root=str(root))
    assert len(runs) == 1
    assert runs[0]["run_id"] == "ld-1"
    assert runs[0]["source"] == "agent" and runs[0]["ducky_name"] == "Level Designer"
    assert all(not str(r.get("run_id") or "").startswith("human:") for r in runs)


def test_grace_after_stop_still_pins_late_disk_edit(env) -> None:
    from frontend.ui_web import live_agent_runs

    root, journal, writer, verse = env
    live_agent_runs.set_live_writer(
        "ld-2",
        {"source": "agent", "run_id": "ld-2", "ducky_name": "Level Designer"},
    )
    live_agent_runs.discard_live_run_id("ld-2")
    watch = HumanWatch()
    watch.tick()
    (verse / "a.verse").write_text("late\n", encoding="utf-8", newline="\n")
    _flush(watch)
    runs = journal.list_runs(project_root=str(root))
    assert [r["run_id"] for r in runs] == ["ld-2"]
    assert runs[0]["ducky_name"] == "Level Designer"


def test_mtime_touch_without_edit_is_not_a_you_row(env) -> None:
    import os

    root, journal, writer, verse = env
    watch = HumanWatch()
    watch.tick()
    os.utime(verse / "a.verse", None)
    _flush(watch)
    assert journal.list_runs(project_root=str(root)) == []


def test_unchanged_files_are_not_reread_every_poll(env, monkeypatch):
    """The 2 s poll must stat, not read, when nothing moved.

    _scan used to read every watched file in full on every pass, so an idle
    panel streamed the island off disk continuously and burned its CPU in the
    kernel. Reads now happen only when mtime or size changed.
    """
    root, _journal, _writer, verse = env
    for i in range(12):
        (verse / f"f{i}.verse").write_text(f"body {i}\n", encoding="utf-8", newline="\n")

    reads: list[str] = []
    real_open = open

    def counting_open(path, *args, **kwargs):
        if str(path).endswith(".verse"):
            reads.append(str(path))
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr("builtins.open", counting_open)

    watch = HumanWatch()
    island = Path(hw.island_root(str(root)))

    watch._scan(island)
    first = len(reads)
    assert first >= 13, "the first pass must read every watched file"

    reads.clear()
    watch._scan(island)
    watch._scan(island)
    assert reads == [], f"unchanged files were re-read: {reads}"

    # A real edit is still picked up, and only that file is read.
    reads.clear()
    target = verse / "f7.verse"
    st = target.stat()
    target.write_text("edited by a human\n", encoding="utf-8", newline="\n")
    os.utime(target, ns=(st.st_mtime_ns + 1_000_000_000, st.st_mtime_ns + 1_000_000_000))
    rows = watch._scan(island)
    assert [Path(p).name for p in reads] == ["f7.verse"]
    assert rows[hw._canon_watch_key("Content/Verse/f7.verse")]["content"] == "edited by a human\n"


def test_scan_does_not_resolve_every_file_each_poll(env, monkeypatch):
    """Each poll used to realpath every watched file twice (a file handle each on
    Windows), so an idle island cost ~2% of a core. The walk is already under the
    resolved island, so the keys need no resolving."""
    root, _journal, _writer, verse = env
    for i in range(100):
        (verse / f"m{i}.verse").write_text(f"m {i}\n", encoding="utf-8", newline="\n")
    watch = HumanWatch()
    island = Path(hw.island_root(str(root)))
    expected = {"Content/Verse/a.verse", *(f"Content/Verse/m{i}.verse" for i in range(100))}

    calls: list[str] = []
    real = os.path.realpath

    def counting(path, *args, **kwargs):
        calls.append(str(path))
        return real(path, *args, **kwargs)

    monkeypatch.setattr(os.path, "realpath", counting)
    for _ in range(2):
        calls.clear()
        assert set(watch._scan(island)) == expected
        assert len(calls) <= 1, f"{len(calls)} realpath calls in one poll"


def test_project_switch_journals_nothing(tmp_path, monkeypatch):
    """Switching the panel project used to diff the new island against the old
    island's file map, so ~4 s later every file of both became a You row."""
    from frontend.ui_web import live_agent_runs

    roots: dict[str, str] = {}
    for name in ("ProjA", "ProjB"):
        verse = tmp_path / name / "Content" / "Verse"
        verse.mkdir(parents=True)
        for i in range(3):
            (verse / f"{name.lower()}{i}.verse").write_text(f"{name} {i}\n", encoding="utf-8", newline="\n")
        roots[name] = os.path.realpath(tmp_path / name)
    current = {"root": roots["ProjA"]}
    journal = FileChangeJournal(lambda r: tmp_path / f"store-{tmp_path.name}-{Path(r).name}")
    writer = ProjectWriter(
        root_resolver=lambda: current["root"],
        path_resolver=lambda rel: os.path.join(current["root"], rel),
        journal=journal,
    )
    runtime.reset_for_tests(writer)
    hw.reset_for_tests()
    live_agent_runs.reset_for_tests()
    monkeypatch.setattr(hw, "STABLE_POLLS", 2)
    try:
        watch = HumanWatch()
        watch.tick()  # seed A
        current["root"] = roots["ProjB"]
        _flush(watch, 3)
        assert journal.list_runs(project_root=roots["ProjB"]) == []
        assert journal.list_runs(project_root=roots["ProjA"]) == []

        # B is watched for real after the switch, and back on A nothing moved.
        (tmp_path / "ProjB" / "Content" / "Verse" / "projb0.verse").write_text(
            "edited\n", encoding="utf-8", newline="\n"
        )
        _flush(watch, 2)
        runs = journal.list_runs(project_root=roots["ProjB"])
        assert [e["path"] for r in runs for e in r["entries"]] == ["Content/Verse/projb0.verse"]
        current["root"] = roots["ProjA"]
        _flush(watch, 3)
        assert journal.list_runs(project_root=roots["ProjA"]) == []
    finally:
        runtime.reset_for_tests(None)
        hw.reset_for_tests()
        live_agent_runs.reset_for_tests()


def test_many_edits_in_one_poll_save_the_index_once(env, monkeypatch):
    root, journal, _writer, verse = env
    for i in range(5):
        (verse / f"e{i}.verse").write_text(f"e {i}\n", encoding="utf-8", newline="\n")
    watch = HumanWatch()
    watch.tick()
    for i in range(5):
        (verse / f"e{i}.verse").write_text(f"edited {i}\n", encoding="utf-8", newline="\n")
    saves: list[int] = []
    real_save = hw._save_saved

    def counting(storage, seen):
        saves.append(len(seen))
        return real_save(storage, seen)

    monkeypatch.setattr(hw, "_save_saved", counting)
    _flush(watch, 2)
    runs = journal.list_runs(project_root=str(root))
    assert sum(len(r["entries"]) for r in runs) == 5
    assert len(saves) == 1, f"index rewritten {len(saves)} times for one poll"


def test_scan_forgets_files_that_disappear(env):
    """The read cache must not pin deleted files in memory forever."""
    root, _journal, _writer, verse = env
    (verse / "gone.verse").write_text("x\n", encoding="utf-8", newline="\n")
    watch = HumanWatch()
    island = Path(hw.island_root(str(root)))
    watch._scan(island)
    assert any(k.endswith("gone.verse") for k in watch._reads)
    (verse / "gone.verse").unlink()
    watch._scan(island)
    assert not any(k.endswith("gone.verse") for k in watch._reads)
