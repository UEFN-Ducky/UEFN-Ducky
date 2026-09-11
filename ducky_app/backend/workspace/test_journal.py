"""FileChangeJournal: per-run ledger, conflicts, revert round trips, export, retention."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from backend.workspace import events, identity, runtime
from backend.workspace.identity import RunContext
from backend.workspace.journal import FileChangeJournal
from backend.workspace.test_schemas import validate
from backend.workspace.writer import ProjectWriter


@pytest.fixture
def env(tmp_path: Path):
    root = tmp_path / "Proj"
    (root / "Content" / "Verse").mkdir(parents=True)
    storage = tmp_path / "appdata" / "changesets" / "proj"
    clock = {"now": 1_000_000.0}
    journal = FileChangeJournal(lambda _root: storage, clock=lambda: clock["now"])
    writer = ProjectWriter.for_root(str(root), journal=journal, clock=lambda: clock["now"])
    runtime.reset_for_tests(writer)
    events.reset_for_tests()
    yield root, storage, journal, writer, clock
    runtime.reset_for_tests(None)
    events.reset_for_tests()


def as_run(run_id: str, **kw):
    return identity.bind(RunContext(run_id=run_id, conv_id=f"conv-{run_id}", ducky_name=kw.pop("ducky", run_id.title()), **kw))


def test_run_document_is_created_lazily_and_validates(env) -> None:
    root, storage, journal, writer, _ = env
    assert not list((storage / "runs").glob("*.json")) if storage.exists() else True
    token = as_run("r1", model="m", group_id="g")
    try:
        res = writer.write_text("Content/Verse/a.verse", "one\n", tool="workspace_write_file")
        res2 = writer.write_text("Content/Verse/a.verse", "two\n", tool="workspace_write_file")
    finally:
        identity.reset(token)
    assert res.changeset == {"run_id": "r1", "seq": 1, "in_lane": None, "conflict": None, "outcome": "ok"}
    assert res2.changeset["seq"] == 2 and res2.warning == ""
    run = journal.get_run("r1", project_root=str(root))
    assert validate("changeset_run", run) == []
    assert run["status"] == "running" and run["ducky_name"] == "R1" and run["group_id"] == "g"
    e1, e2 = run["entries"]
    assert e1["op"] == "write" and e1["before_blob"] is None and e1["after_blob"]
    assert e2["before_blob"] == e1["after_blob"]
    assert journal.entry_contents("r1", 2, project_root=str(root)) == {
        "path": "Content/Verse/a.verse", "before": "one\n", "after": "two\n"
    }


def test_user_writes_land_in_the_human_bucket(env, monkeypatch) -> None:
    root, storage, journal, writer, _ = env
    for key in RunContext().to_env():
        monkeypatch.delenv(key, raising=False)
    writer.write_text("Content/Verse/a.verse", "user\n", tool="panel_save")
    runs = journal._iter_run_docs(storage)  # noqa: SLF001 — backend-neutral run listing
    assert len(runs) == 1
    run = journal.list_runs(project_root=str(root))[0]
    assert run["run_id"].startswith("human:")
    assert run["source"] == "user" and run["ducky_name"] == "You" and run["status"] == "done"
    index = journal._load_index(storage)  # noqa: SLF001 — rows or index.json
    assert index["Content/Verse/a.verse"]["source"] == "user"
    assert index["Content/Verse/a.verse"]["run_id"].startswith("human:")


def test_end_run_closes_and_prunes(env) -> None:
    root, storage, journal, writer, clock = env
    token = as_run("r1")
    try:
        writer.write_text("Content/Verse/a.verse", "x\n")
    finally:
        identity.reset(token)
    journal.end_run("r1", "done", project_root=str(root))
    run = journal.get_run("r1", project_root=str(root))
    assert run["status"] == "done" and run["ended"] == clock["now"]
    journal.end_run("missing", "done", project_root=str(root))  # no doc: no error


def test_close_orphan_runs_skips_live_and_closes_dead(env) -> None:
    root, storage, journal, writer, _ = env
    for rid in ("dead", "live"):
        token = as_run(rid)
        try:
            writer.write_text(f"Content/Verse/{rid}.verse", f"{rid}\n")
        finally:
            identity.reset(token)
    closed = journal.close_orphan_runs(project_root=str(root), live_run_ids={"live"})
    assert closed == [{"run_id": "dead", "conv_id": "conv-dead"}]
    assert journal.get_run("dead", project_root=str(root))["status"] == "cancelled"
    assert journal.get_run("live", project_root=str(root))["status"] == "running"


def test_stale_base_conflict_is_flagged_not_denied(env) -> None:
    root, storage, journal, writer, _ = env
    seen: list[dict] = []
    events.register_sink(seen.append)
    (root / "Content" / "Verse" / "a.verse").write_text("base\n", encoding="utf-8")
    hacker = as_run("hacker", ducky="Hacker")
    try:
        writer.note_read("Content/Verse/a.verse", "base\n")
    finally:
        identity.reset(hacker)
    solo = as_run("solo", ducky="Solo")
    try:
        writer.write_text("Content/Verse/a.verse", "solo edit\n")
    finally:
        identity.reset(solo)
    hacker = as_run("hacker", ducky="Hacker")
    try:
        res = writer.write_text("Content/Verse/a.verse", "hacker edit\n")
    finally:
        identity.reset(hacker)
    assert (root / "Content" / "Verse" / "a.verse").read_text(encoding="utf-8") == "hacker edit\n"
    assert res.changeset["conflict"]["kind"] == "stale_base"
    assert res.changeset["conflict"]["other_ducky"] == "Solo"
    assert "changed by Solo" in res.warning
    # Every attributed row announces itself (live Changes tab); the conflict rides after it.
    assert [e["type"] for e in seen] == ["changeset_row", "changeset_row", "file_guard"]
    assert seen[1] == {"type": "changeset_row", "run_id": "hacker", "conv_id": "conv-hacker", "path": "Content/Verse/a.verse", "seq": 1}
    assert seen[-1]["kind"] == "conflict"
    run = journal.get_run("hacker", project_root=str(root))
    assert run["entries"][0]["conflict"]["kind"] == "stale_base"


def test_concurrent_writer_conflict_only_while_other_run_is_open(env) -> None:
    root, storage, journal, writer, _ = env
    a = as_run("a")
    try:
        writer.write_text("Content/Verse/shared.verse", "a1\n")
    finally:
        identity.reset(a)
    b = as_run("b")
    try:
        res = writer.write_text("Content/Verse/shared.verse", "b1\n")
    finally:
        identity.reset(b)
    assert res.changeset["conflict"]["kind"] == "concurrent_writer"
    journal.end_run("b", "done", project_root=str(root))
    journal.end_run("a", "done", project_root=str(root))
    c = as_run("c")
    try:
        res2 = writer.write_text("Content/Verse/shared.verse", "c1\n")
    finally:
        identity.reset(c)
    assert res2.changeset["conflict"] is None


def test_revert_run_restores_files_newest_first(env) -> None:
    root, storage, journal, writer, _ = env
    seen: list[dict] = []
    events.register_sink(seen.append)
    (root / "Content" / "Verse" / "a.verse").write_text("orig\n", encoding="utf-8")
    token = as_run("r1")
    try:
        writer.write_text("Content/Verse/a.verse", "v1\n")
        writer.write_text("Content/Verse/a.verse", "v2\n")
        writer.create("Content/Verse/new.verse", "new\n")
    finally:
        identity.reset(token)
    result = journal.revert_run("r1", project_root=str(root))
    assert result["ok"] and result["reverted"] == [1, 2, 3] and result["skipped_modified"] == []
    assert (root / "Content" / "Verse" / "a.verse").read_text(encoding="utf-8") == "orig\n"
    assert not (root / "Content" / "Verse" / "new.verse").exists()
    run = journal.get_run("r1", project_root=str(root))
    assert run["status"] == "reverted" and all(e["reverted"] for e in run["entries"])
    assert all(e["reverted_by_run"].startswith("revert:") for e in run["entries"])
    revert_runs = [r for r in journal.list_runs(project_root=str(root)) if r["run_id"].startswith("revert:")]
    assert len(revert_runs) == 1 and revert_runs[0]["source"] == "revert"
    assert revert_runs[0].get("conv_id") == "conv-r1"
    assert any(r["run_id"].startswith("revert:") for r in journal.list_runs(project_root=str(root), conv_id="conv-r1"))
    assert seen[-1]["type"] == "files_reverted" and set(seen[-1]["paths"]) == {"Content/Verse/a.verse", "Content/Verse/new.verse"}
    assert revert_runs[0].get("reverts_run_id") == "r1"


def test_write_after_revert_unlocks_the_run(env) -> None:
    root, _, journal, writer, _ = env
    (root / "Content" / "Verse" / "a.verse").write_text("orig\n", encoding="utf-8")
    token = as_run("r1")
    try:
        writer.write_text("Content/Verse/a.verse", "v1\n")
    finally:
        identity.reset(token)
    journal.revert_run("r1", project_root=str(root))
    assert journal.get_run("r1", project_root=str(root))["status"] == "reverted"
    token = as_run("r1")
    try:
        writer.write_text("Content/Verse/a.verse", "again\n")
    finally:
        identity.reset(token)
    run = journal.get_run("r1", project_root=str(root))
    assert run["status"] == "running"
    assert all(not e.get("reverted") for e in run["entries"])


def test_revert_of_revert_restores_agent_work(env) -> None:
    root, storage, journal, writer, _ = env
    (root / "Content" / "Verse" / "a.verse").write_text("orig\n", encoding="utf-8")
    token = as_run("r1")
    try:
        writer.write_text("Content/Verse/a.verse", "v2\n")
    finally:
        identity.reset(token)
    first = journal.revert_run("r1", project_root=str(root))
    assert (root / "Content" / "Verse" / "a.verse").read_text(encoding="utf-8") == "orig\n"
    revert_id = first["revert_run_id"]
    assert revert_id
    second = journal.revert_run(revert_id, project_root=str(root))
    assert second["ok"] and second["reverted"]
    assert (root / "Content" / "Verse" / "a.verse").read_text(encoding="utf-8") == "v2\n"
    original = journal.get_run("r1", project_root=str(root))
    assert original["status"] == "done"
    assert all(not e.get("reverted") for e in original["entries"])
    assert all(not e.get("reverted_by_run") for e in original["entries"])
    assert journal.get_run(revert_id, project_root=str(root))["status"] == "reverted"
    assert journal.get_run(revert_id, project_root=str(root)).get("conv_id") == "conv-r1"
    redo_runs = [r for r in journal.list_runs(project_root=str(root), conv_id="conv-r1") if r["run_id"] != "r1"]
    assert any(r["run_id"] == revert_id or r.get("reverts_run_id") == revert_id for r in redo_runs)


def test_later_human_edit_does_not_block_agent_revert(env) -> None:
    root, _, journal, writer, clock = env
    (root / "Content" / "Verse" / "a.verse").write_text("orig\n", encoding="utf-8")
    token = as_run("r1")
    try:
        writer.write_text("Content/Verse/a.verse", "from-agent\n")
    finally:
        identity.reset(token)
    clock["now"] += 1
    writer.write_text("Content/Verse/a.verse", "from-you\n", tool="panel_save")
    result = journal.revert_run("r1", project_root=str(root))
    assert result["blocked_by"] == []
    assert result["reverted"] == []
    assert result["skipped_modified"] == [{"seq": 1, "path": "Content/Verse/a.verse"}]
    assert (root / "Content" / "Verse" / "a.verse").read_text(encoding="utf-8") == "from-you\n"


def test_later_writer_in_another_chat_blocks_file_revert(env) -> None:
    root, _, journal, writer, clock = env
    (root / "Content" / "Verse" / "a.verse").write_text("orig\n", encoding="utf-8")
    token = as_run("r1")
    try:
        writer.write_text("Content/Verse/a.verse", "from-r1\n")
    finally:
        identity.reset(token)
    clock["now"] += 1
    token = as_run("r2")
    try:
        writer.write_text("Content/Verse/a.verse", "from-r2\n")
    finally:
        identity.reset(token)
    result = journal.revert_run("r1", project_root=str(root))
    assert result["ok"] is False
    assert result["reverted"] == []
    [blocker] = result["blocked_by"]
    assert blocker["other_conv_id"] == "conv-r2"
    assert blocker["other_run_id"] == "r2"
    assert (root / "Content" / "Verse" / "a.verse").read_text(encoding="utf-8") == "from-r2\n"


def test_revert_skips_files_modified_since_unless_forced(env) -> None:
    root, storage, journal, writer, _ = env
    token = as_run("r1")
    try:
        writer.write_text("Content/Verse/a.verse", "v1\n")
    finally:
        identity.reset(token)
    (root / "Content" / "Verse" / "a.verse").write_text("someone else\n", encoding="utf-8")
    result = journal.revert_run("r1", project_root=str(root))
    assert result["ok"] is False
    assert result["reverted"] == [] and result["skipped_modified"] == [{"seq": 1, "path": "Content/Verse/a.verse"}]
    assert journal.get_run("r1", project_root=str(root))["status"] == "running"
    forced = journal.revert_run("r1", project_root=str(root), force=True)
    assert forced["ok"] and forced["reverted"] == [1]
    assert not (root / "Content" / "Verse" / "a.verse").exists()  # created by r1, so revert removes it


def test_revert_does_not_mark_if_the_file_is_still_there(env, monkeypatch) -> None:
    root, _, journal, writer, _ = env
    (root / "Content" / "Verse" / "a.verse").write_text("orig\n", encoding="utf-8")
    token = as_run("r1")
    try:
        writer.write_text("Content/Verse/a.verse", "v1\n")
    finally:
        identity.reset(token)
    monkeypatch.setattr(writer, "write_text", lambda *a, **k: None)
    result = journal.revert_run("r1", project_root=str(root))
    assert result["ok"] is False and result["reverted"] == []
    assert "did not restore" in result["errors"][0]
    assert journal.get_run("r1", project_root=str(root))["entries"][0].get("reverted") is not True
    assert (root / "Content" / "Verse" / "a.verse").read_text(encoding="utf-8") == "v1\n"


def test_revert_entry_of_delete_and_move(env) -> None:
    root, storage, journal, writer, _ = env
    verse = root / "Content" / "Verse"
    (verse / "gone.verse").write_text("bye\n", encoding="utf-8")
    (verse / "old.verse").write_text("mv\n", encoding="utf-8")
    token = as_run("r1")
    try:
        writer.path_op("delete", "Content/Verse/gone.verse", perform=(verse / "gone.verse").unlink)

        def _move() -> None:
            (verse / "Sub").mkdir(exist_ok=True)
            os.replace(verse / "old.verse", verse / "Sub" / "new.verse")

        writer.path_op("move", "Content/Verse/Sub/new.verse", source="Content/Verse/old.verse", perform=_move)
    finally:
        identity.reset(token)
    r = journal.revert_entry("r1", 1, project_root=str(root))
    assert r["reverted"] == [1] and (verse / "gone.verse").read_text(encoding="utf-8") == "bye\n"
    r2 = journal.revert_entry("r1", 2, project_root=str(root))
    assert r2["reverted"] == [2] and (verse / "old.verse").is_file() and not (verse / "Sub" / "new.verse").exists()
    assert journal.get_run("r1", project_root=str(root))["status"] == "reverted"
    again = journal.revert_entry("r1", 2, project_root=str(root))
    assert again["reverted"] == [] and "already reverted" in again["errors"][0]


def test_revert_created_file_removes_empty_folder(env, monkeypatch) -> None:
    root, _, journal, writer, _ = env
    monkeypatch.setattr("backend.workspace.journal.kick_verse_compile", lambda: None)
    monkeypatch.setattr("backend.workspace.journal._revert_uefn_for_deleted_verse", lambda *a, **k: None)
    token = as_run("r1")
    try:
        writer.write_text("Content/Verse/SnakeGame/game.verse", "class snake_game_device:\n    x: int = 0\n")
    finally:
        identity.reset(token)
    assert (root / "Content" / "Verse" / "SnakeGame" / "game.verse").is_file()
    result = journal.revert_run("r1", project_root=str(root))
    assert result["ok"] and result["reverted"] == [1]
    assert not (root / "Content" / "Verse" / "SnakeGame").exists()
    assert (root / "Content" / "Verse").is_dir()


def test_revert_created_file_keeps_folder_if_sibling_remains(env, monkeypatch) -> None:
    root, _, journal, writer, _ = env
    monkeypatch.setattr("backend.workspace.journal.kick_verse_compile", lambda: None)
    monkeypatch.setattr("backend.workspace.journal._revert_uefn_for_deleted_verse", lambda *a, **k: None)
    (root / "Content" / "Verse" / "SnakeGame").mkdir()
    (root / "Content" / "Verse" / "SnakeGame" / "keep.verse").write_text("keep\n", encoding="utf-8")
    token = as_run("r1")
    try:
        writer.write_text("Content/Verse/SnakeGame/game.verse", "new\n")
    finally:
        identity.reset(token)
    journal.revert_run("r1", project_root=str(root))
    assert not (root / "Content" / "Verse" / "SnakeGame" / "game.verse").exists()
    assert (root / "Content" / "Verse" / "SnakeGame" / "keep.verse").is_file()


def test_deleted_verse_device_asks_uefn_to_drop_the_class(monkeypatch) -> None:
    posted: list[tuple[str, dict, str]] = []
    monkeypatch.setattr(
        "backend.workspace.plugin_revert.post_inverse_step",
        lambda cmd, params, program="uefn": posted.append((cmd, dict(params), program)),
    )
    from backend.workspace.journal import _revert_uefn_for_deleted_verse

    _revert_uefn_for_deleted_verse(
        "Content/Verse/SnakeGame/game.verse",
        "class snake_game_device:\n",
        r"C:\islands\ExampleProject1",
    )
    ids = {(p[1].get("kind"), p[1].get("id")) for p in posted}
    assert ("asset", "/ExampleProject1/Verse/SnakeGame/snake_game_device") in ids
    assert ("actor", "snake_game_device") in ids


def test_revert_entry_reverts_every_write_to_that_file(env) -> None:
    root, storage, journal, writer, _ = env
    (root / "Content" / "Verse" / "a.verse").write_text("orig\n", encoding="utf-8")
    token = as_run("r1")
    try:
        writer.write_text("Content/Verse/a.verse", "v1\n")
        writer.write_text("Content/Verse/a.verse", "v2\n")
        writer.write_text("Content/Verse/b.verse", "other\n")
    finally:
        identity.reset(token)
    result = journal.revert_entry("r1", 2, project_root=str(root))
    assert result["reverted"] == [1, 2]
    assert (root / "Content" / "Verse" / "a.verse").read_text(encoding="utf-8") == "orig\n"
    assert (root / "Content" / "Verse" / "b.verse").is_file()
    assert journal.get_run("r1", project_root=str(root))["status"] == "partially_reverted"


def test_step_revert_restores_only_that_write(env) -> None:
    root, _storage, journal, writer, _ = env
    (root / "Content" / "Verse" / "a.verse").write_text("orig\n", encoding="utf-8")
    token = as_run("r1")
    try:
        writer.write_text("Content/Verse/a.verse", "v1\n")
        writer.write_text("Content/Verse/a.verse", "v2\n")
        writer.write_text("Content/Verse/a.verse", "v3\n")
    finally:
        identity.reset(token)
    result = journal.revert_entry("r1", 3, project_root=str(root), step=True)
    assert result["ok"] and result["reverted"] == [3]
    assert (root / "Content" / "Verse" / "a.verse").read_text(encoding="utf-8") == "v2\n"
    run = journal.get_run("r1", project_root=str(root))
    assert [e["reverted"] for e in run["entries"]] == [False, False, True]
    assert run["status"] == "partially_reverted"


def test_step_revert_blocks_when_a_later_write_exists(env) -> None:
    root, _storage, journal, writer, _ = env
    (root / "Content" / "Verse" / "a.verse").write_text("orig\n", encoding="utf-8")
    token = as_run("r1")
    try:
        writer.write_text("Content/Verse/a.verse", "v1\n")
        writer.write_text("Content/Verse/a.verse", "v2\n")
        writer.write_text("Content/Verse/a.verse", "v3\n")
    finally:
        identity.reset(token)
    result = journal.revert_entry("r1", 1, project_root=str(root), step=True)
    assert result["ok"] is False
    assert result["reverted"] == []
    [blocker] = result["blocked_by"]
    assert blocker["other_seq"] == 3
    assert blocker["same_run"] is True
    assert blocker["other_run_id"] == "r1"
    assert (root / "Content" / "Verse" / "a.verse").read_text(encoding="utf-8") == "v3\n"


def test_step_revert_walks_newest_first_back_to_orig(env) -> None:
    root, _storage, journal, writer, _ = env
    (root / "Content" / "Verse" / "a.verse").write_text("orig\n", encoding="utf-8")
    token = as_run("r1")
    try:
        writer.write_text("Content/Verse/a.verse", "v1\n")
        writer.write_text("Content/Verse/a.verse", "v2\n")
        writer.write_text("Content/Verse/a.verse", "v3\n")
    finally:
        identity.reset(token)
    assert journal.revert_entry("r1", 3, project_root=str(root), step=True)["reverted"] == [3]
    assert (root / "Content" / "Verse" / "a.verse").read_text(encoding="utf-8") == "v2\n"
    assert journal.revert_entry("r1", 2, project_root=str(root), step=True)["reverted"] == [2]
    assert (root / "Content" / "Verse" / "a.verse").read_text(encoding="utf-8") == "v1\n"
    assert journal.revert_entry("r1", 1, project_root=str(root), step=True)["reverted"] == [1]
    assert (root / "Content" / "Verse" / "a.verse").read_text(encoding="utf-8") == "orig\n"
    assert journal.get_run("r1", project_root=str(root))["status"] == "reverted"


def test_after_step_revert_path_revert_restores_the_rest(env) -> None:
    root, _storage, journal, writer, _ = env
    (root / "Content" / "Verse" / "a.verse").write_text("orig\n", encoding="utf-8")
    token = as_run("r1")
    try:
        writer.write_text("Content/Verse/a.verse", "v1\n")
        writer.write_text("Content/Verse/a.verse", "v2\n")
        writer.write_text("Content/Verse/b.verse", "other\n")
    finally:
        identity.reset(token)
    journal.revert_entry("r1", 2, project_root=str(root), step=True)
    result = journal.revert_entry("r1", 1, project_root=str(root))
    assert result["reverted"] == [1]
    assert (root / "Content" / "Verse" / "a.verse").read_text(encoding="utf-8") == "orig\n"
    assert (root / "Content" / "Verse" / "b.verse").is_file()


def test_export_matches_schema(env) -> None:
    root, storage, journal, writer, _ = env
    token = as_run("r1", profile_id="hacker", model="anthropic:x")
    try:
        writer.write_text("Content/Verse/a.verse", "v1\n")
    finally:
        identity.reset(token)
    export = journal.export_run("r1", project_root=str(root))
    assert validate("changeset_export", export) == []
    assert export["author"] == {"id": "hacker", "name": "R1", "model": "anthropic:x"}
    assert export["files"][0]["path"] == "Content/Verse/a.verse" and export["files"][0]["op"] == "write"


def test_list_runs_filters_and_orders(env) -> None:
    root, storage, journal, writer, clock = env
    for rid, conv_kw in (("r1", {"group_id": "g1"}), ("r2", {"group_id": "g2"})):
        clock["now"] += 10
        token = as_run(rid, **conv_kw)
        try:
            writer.write_text(f"Content/Verse/{rid}.verse", "x\n")
        finally:
            identity.reset(token)
    runs = journal.list_runs(project_root=str(root))
    assert [r["run_id"] for r in runs] == ["r2", "r1"]
    assert [r["run_id"] for r in journal.list_runs(project_root=str(root), group_id="g1")] == ["r1"]
    assert [r["run_id"] for r in journal.list_runs(project_root=str(root), conv_id="conv-r2")] == ["r2"]


def test_list_runs_empty_storage_does_not_create_dirs(tmp_path: Path) -> None:
    root = tmp_path / "Proj"
    (root / "Content").mkdir(parents=True)
    storage = tmp_path / "store"
    journal = FileChangeJournal(lambda _r: storage)
    assert journal.list_runs(project_root=str(root)) == []
    assert not storage.exists()


def test_prune_drops_old_closed_runs_and_orphan_blobs(env) -> None:
    root, storage, journal, writer, clock = env
    token = as_run("old")
    try:
        writer.write_text("Content/Verse/a.verse", "old\n")
    finally:
        identity.reset(token)
    journal.end_run("old", "done", project_root=str(root))
    clock["now"] += 40 * 86400
    token = as_run("fresh")
    try:
        writer.write_text("Content/Verse/b.verse", "fresh\n")
    finally:
        identity.reset(token)
    stats = journal.prune(project_root=str(root))
    assert stats == {"removed_runs": 1, "removed_blobs": 1}
    assert [r["run_id"] for r in journal.list_runs(project_root=str(root))] == ["fresh"]


def test_delete_runs_forgets_ledger_not_files(env) -> None:
    root, storage, journal, writer, _ = env
    token = as_run("r1")
    try:
        writer.write_text("Content/Verse/a.verse", "keep\n")
    finally:
        identity.reset(token)
    journal.end_run("r1", "done", project_root=str(root))
    token = as_run("r2")
    try:
        writer.write_text("Content/Verse/b.verse", "also\n")
    finally:
        identity.reset(token)
    journal.end_run("r2", "done", project_root=str(root))
    stats = journal.delete_runs(project_root=str(root), conv_id="conv-r1")
    assert stats["removed_runs"] == 1
    assert [r["run_id"] for r in journal.list_runs(project_root=str(root))] == ["r2"]
    assert (root / "Content" / "Verse" / "a.verse").read_text(encoding="utf-8") == "keep\n"
    index = journal._load_index(storage)  # noqa: SLF001 — rows or index.json
    assert "Content/Verse/a.verse" not in index
    assert "Content/Verse/b.verse" in index


def test_archive_hides_from_active_list_until_deleted(env) -> None:
    root, _storage, journal, writer, _ = env
    token = as_run("r1")
    try:
        writer.write_text("Content/Verse/a.verse", "keep\n")
    finally:
        identity.reset(token)
    journal.end_run("r1", "done", project_root=str(root))
    assert journal.set_archived(project_root=str(root), run_id="r1") == {"updated": 1}
    assert journal.list_runs(project_root=str(root)) == []
    archived = journal.list_runs(project_root=str(root), archived=True)
    assert [r["run_id"] for r in archived] == ["r1"] and archived[0]["archived"] is True
    assert journal.list_runs(project_root=str(root), archived=None)[0]["run_id"] == "r1"
    with pytest.raises(ValueError, match="archived"):
        journal.revert_run("r1", project_root=str(root))
    assert journal.entry_contents("r1", 1, project_root=str(root))["after"] == "keep\n"
    assert journal.set_archived(project_root=str(root), run_id="r1", archived=False) == {"updated": 1}
    assert [r["run_id"] for r in journal.list_runs(project_root=str(root))] == ["r1"]
    journal.set_archived(project_root=str(root), run_id="r1")
    stats = journal.delete_runs(project_root=str(root), archived_only=True)
    assert stats["removed_runs"] == 1
    assert journal.list_runs(project_root=str(root), archived=True) == []
    assert (root / "Content" / "Verse" / "a.verse").read_text(encoding="utf-8") == "keep\n"


def test_delete_archived_only_leaves_active_runs(env) -> None:
    root, _storage, journal, writer, _ = env
    token = as_run("r1")
    try:
        writer.write_text("Content/Verse/a.verse", "a\n")
    finally:
        identity.reset(token)
    journal.end_run("r1", "done", project_root=str(root))
    stats = journal.delete_runs(project_root=str(root), archived_only=True)
    assert stats["removed_runs"] == 0
    assert [r["run_id"] for r in journal.list_runs(project_root=str(root))] == ["r1"]


def test_delete_runs_skips_open_run(env) -> None:
    root, _storage, journal, writer, _ = env
    token = as_run("r1")
    try:
        writer.write_text("Content/Verse/a.verse", "live\n")
    finally:
        identity.reset(token)
    stats = journal.delete_runs(project_root=str(root), conv_id="conv-r1")
    assert stats["removed_runs"] == 0
    assert journal.get_run("r1", project_root=str(root))["status"] == "running"


def test_delete_entries_drops_failed_keeps_ok(env) -> None:
    root, storage, journal, writer, _ = env
    token = as_run("r1")
    try:
        writer.write_text("Content/Verse/a.verse", "ok\n")
    finally:
        identity.reset(token)
    run = journal.get_run("r1", project_root=str(root))
    failed = dict(run["entries"][0])
    failed["seq"] = 2
    failed["outcome"] = "failed"
    failed["reason"] = "STALE REFLECTION"
    run["entries"].append(failed)
    journal._save_run(storage, run)  # noqa: SLF001
    stats = journal.delete_entries("r1", [1, 2], project_root=str(root))
    assert stats == {"removed": 1, "kept": 1}
    assert [e["seq"] for e in journal.get_run("r1", project_root=str(root))["entries"]] == [1]


def test_disabled_journal_records_nothing(tmp_path: Path) -> None:
    root = tmp_path / "Proj"
    (root / "Content").mkdir(parents=True)
    journal = FileChangeJournal(lambda _r: tmp_path / "store", enabled=lambda: False)
    writer = ProjectWriter.for_root(str(root), journal=journal)
    token = as_run("r1")
    try:
        res = writer.write_text("Content/a.txt", "x")
    finally:
        identity.reset(token)
    assert res.changeset == {} and not (tmp_path / "store").exists()


def test_revert_survives_history_pruning(env, monkeypatch) -> None:
    """The journal's blobs are the revert source; editor history retention is irrelevant."""
    root, storage, journal, writer, _ = env
    token = as_run("r1")
    try:
        writer.write_text("Content/Verse/a.verse", "v0\n")
        for i in range(1, 130):
            writer.write_text("Content/Verse/a.verse", f"v{i}\n")
    finally:
        identity.reset(token)
    result = journal.revert_run("r1", project_root=str(root))
    assert result["ok"] and len(result["reverted"]) == 130
    assert not (root / "Content" / "Verse" / "a.verse").exists()


def test_revert_heals_content_prefixed_path_when_root_is_content(tmp_path: Path) -> None:
    island = tmp_path / "Proj"
    content = island / "Content"
    (content / "Verse").mkdir(parents=True)
    (content / "Verse" / "x.verse").write_text("orig\n", encoding="utf-8")
    storage = tmp_path / "store"
    journal = FileChangeJournal(lambda _r: storage)
    writer = ProjectWriter.for_root(str(content), journal=journal)
    runtime.reset_for_tests(writer)
    token = as_run("r1")
    try:
        writer.write_text("Verse/x.verse", "v2\n")
    finally:
        identity.reset(token)
    run = journal.get_run("r1", project_root=str(content))
    run["entries"][0]["path"] = "Content/Verse/x.verse"
    journal._save_run(storage, run)  # noqa: SLF001 — simulate an old ledger row
    result = journal.revert_run("r1", project_root=str(content))
    runtime.reset_for_tests(None)
    assert result["ok"] and result["reverted"] == [1]
    assert (content / "Verse" / "x.verse").read_text(encoding="utf-8") == "orig\n"


def test_revert_create_reports_skipped_when_file_is_gone(env) -> None:
    root, _, journal, writer, _ = env
    token = as_run("r1")
    try:
        writer.create("Content/Verse/new.verse", "new\n")
    finally:
        identity.reset(token)
    (root / "Content" / "Verse" / "new.verse").unlink()
    result = journal.revert_run("r1", project_root=str(root))
    assert result["ok"] is False
    assert result["reverted"] == []
    assert result["skipped_modified"] == [{"seq": 1, "path": "Content/Verse/new.verse"}]
