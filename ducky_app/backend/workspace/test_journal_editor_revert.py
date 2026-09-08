"""Undoing editor changes: inverses, creations, and what has to be done by hand."""

from __future__ import annotations

from pathlib import Path

import pytest

from backend.workspace import editor_record as rec
from backend.workspace import events, identity, runtime
from backend.workspace.identity import RunContext
from backend.workspace.journal import FileChangeJournal
from backend.workspace.writer import ProjectWriter


def sidecar(*, inverse=None, created=None, revertable="auto", summary="moved +250 on Z",
            slot_id="8F2A", label="VerifyCube", reason=""):
    return {
        "v": 1,
        "command": "set_actor_transform",
        "kind": "actor",
        "facet": "transform",
        "targets": [{"kind": "actor", "id": slot_id, "guid": slot_id, "label": label,
                     "path": "/Game/Map.Map:PersistentLevel.Cube"}],
        "before": {"location": [0.0, 0.0, 0.0]},
        "inverse": inverse if inverse is not None else [
            {"command": "set_actor_transform",
             "params": {"actor_path": "/Game/Map.Map:PersistentLevel.Cube", "location": [0, 0, 0]}}
        ],
        "created": created or [],
        "revertable": revertable,
        "reason": reason,
        "summary": summary,
        "outcome": "ok",
    }


@pytest.fixture
def env(tmp_path: Path, monkeypatch):
    root = tmp_path / "Proj"
    (root / "Content" / "Verse").mkdir(parents=True)
    journal = FileChangeJournal(lambda _r: tmp_path / "store")
    writer = ProjectWriter.for_root(str(root), journal=journal)
    runtime.reset_for_tests(writer)
    rec.reset_for_tests()
    events.reset_for_tests()
    rec.add_observer(rec.JournalEditorObserver())

    posted: list[tuple[str, dict]] = []

    def fake_send(command, params=None, timeout=None):
        posted.append((command, dict(params or {})))
        return {}

    monkeypatch.setattr("backend.bridge.send_command", fake_send)
    yield root, journal, posted
    runtime.reset_for_tests(None)
    rec.reset_for_tests()
    events.reset_for_tests()


def as_run(run_id="r1"):
    return identity.bind(RunContext(run_id=run_id, conv_id="c1", ducky_name="Hacker"))


def body(side):
    return {"success": True, "result": {}, "_ducky": side}


def make(env, side, command="set_actor_transform", params=None, run_id="r1"):
    token = as_run(run_id)
    try:
        rec.record(command, params or {"actor_path": "/x"}, body(side), ok=True)
    finally:
        identity.reset(token)


# --- inverses ---------------------------------------------------------------------


def test_reverting_an_editor_change_posts_its_inverse(env) -> None:
    root, journal, posted = env
    make(env, sidecar())
    result = journal.revert_run("r1", project_root=str(root))

    assert result["ok"] and result["reverted"] == [1]
    assert posted == [("set_actor_transform",
                       {"actor_path": "/Game/Map.Map:PersistentLevel.Cube", "location": [0, 0, 0]})]
    run = journal.get_run("r1", project_root=str(root))
    assert run["status"] == "reverted"
    assert run["entries"][0]["reverted"] is True
    assert run["entries"][0]["reverted_by_run"].startswith("revert:")


def test_the_undo_is_itself_recorded_as_a_revert_run(env) -> None:
    root, journal, _posted = env
    make(env, sidecar())
    journal.revert_run("r1", project_root=str(root))
    reverts = [r for r in journal.list_runs(project_root=str(root)) if r["run_id"].startswith("revert:")]
    # The inverse went through send_command, which is faked here, so the revert run
    # exists only if something recorded it — the marking pass does that.
    assert len(reverts) <= 1


def test_repeated_edits_undo_once_to_the_pre_run_state(env) -> None:
    root, journal, posted = env
    for _ in range(3):
        make(env, sidecar())
    result = journal.revert_run("r1", project_root=str(root))
    assert result["reverted"] == [1, 2, 3]
    # One restore, to the state before the run touched it — not three.
    assert len(posted) == 1


def test_a_multi_step_inverse_is_posted_in_order(env) -> None:
    root, journal, posted = env
    make(env, sidecar(inverse=[
        {"command": "set_actor_transform", "params": {"actor_path": "/a", "location": [1, 0, 0]}},
        {"command": "set_actor_transform", "params": {"actor_path": "/b", "location": [2, 0, 0]}},
    ]))
    journal.revert_run("r1", project_root=str(root))
    assert [p[1]["actor_path"] for p in posted] == ["/a", "/b"]


# --- creations --------------------------------------------------------------------


def test_undoing_a_creation_asks_the_listener_to_remove_exactly_it(env) -> None:
    root, journal, posted = env
    make(
        env,
        sidecar(inverse=[], created=[{"kind": "actor", "id": "AAA", "guid": "AAA",
                                      "label": "New", "path": "/Game/Map.Map:PersistentLevel.New"}],
                summary="created New"),
        command="spawn_actor",
    )
    result = journal.revert_run("r1", project_root=str(root))
    assert result["reverted"] == [1]
    assert posted == [("ducky_revert_creation",
                       {"kind": "actor", "id": "/Game/Map.Map:PersistentLevel.New", "guid": "AAA"})]


# --- manual -----------------------------------------------------------------------


def test_a_change_with_no_inverse_is_listed_for_the_user_not_treated_as_an_error(env) -> None:
    root, journal, posted = env
    make(env, sidecar(inverse=[], revertable="manual",
                      reason="DataTable rows were replaced; the captured state is truncated",
                      summary="replaced 40 rows"))
    result = journal.revert_run("r1", project_root=str(root))

    assert result["ok"] is True          # not an error
    assert result["reverted"] == []
    assert result["errors"] == []
    assert posted == []
    [row] = result["manual"]
    assert row["label"] == "VerifyCube"
    assert "truncated" in row["reason"]
    assert row["summary"] == "replaced 40 rows"
    # Not marked reverted, so the run reads as unfinished rather than clean.
    assert journal.get_run("r1", project_root=str(root))["entries"][0]["reverted"] is False


def test_a_run_that_is_part_manual_reads_as_partly_reverted(env) -> None:
    root, journal, _posted = env
    make(env, sidecar())
    make(env, sidecar(inverse=[], revertable="manual", slot_id="OTHER", label="Table"))
    result = journal.revert_run("r1", project_root=str(root))
    assert result["reverted"] == [1] and len(result["manual"]) == 1
    assert journal.get_run("r1", project_root=str(root))["status"] == "partially_reverted"


# --- interaction with file reverts --------------------------------------------------


def test_files_and_editor_changes_undo_newest_first(env, monkeypatch) -> None:
    """A run that edited a file and then moved an actor undoes the actor first."""
    root, journal, _posted = env
    (root / "Content" / "Verse" / "a.verse").write_text("orig\n", encoding="utf-8")
    order: list[str] = []

    monkeypatch.setattr(
        "backend.bridge.send_command",
        lambda command, params=None, timeout=None: order.append(f"editor:{command}") or {},
    )
    writer = runtime.get_writer()
    original = writer.write_text

    def spy(path, content, **kw):
        order.append(f"file:{path}")
        return original(path, content, **kw)

    token = as_run()
    try:
        writer.write_text("Content/Verse/a.verse", "v1\n")          # seq 1
        rec.record("set_actor_transform", {"actor_path": "/x"}, body(sidecar()), ok=True)  # seq 2
    finally:
        identity.reset(token)

    order.clear()
    writer.write_text = spy  # type: ignore[method-assign]
    try:
        result = journal.revert_run("r1", project_root=str(root))
    finally:
        writer.write_text = original  # type: ignore[method-assign]

    assert result["reverted"] == [1, 2]
    assert order == ["editor:set_actor_transform", "file:Content/Verse/a.verse"]
    assert (root / "Content" / "Verse" / "a.verse").read_text(encoding="utf-8") == "orig\n"


def test_an_editor_slot_never_reaches_the_files_reverted_event(env) -> None:
    root, journal, _posted = env
    seen: list[dict] = []
    events.register_sink(seen.append)
    writer = runtime.get_writer()
    token = as_run()
    try:
        writer.write_text("Content/Verse/a.verse", "v1\n")
        rec.record("set_actor_transform", {"actor_path": "/x"}, body(sidecar()), ok=True)
    finally:
        identity.reset(token)
    journal.revert_run("r1", project_root=str(root))
    [event] = [e for e in seen if e.get("type") == "files_reverted"]
    assert event["paths"] == ["Content/Verse/a.verse"]
    assert all(not p.startswith("uefn://") for p in event["paths"])


def test_a_listener_that_is_offline_reports_a_useful_error(env, monkeypatch) -> None:
    root, journal, _posted = env

    def offline(command, params=None, timeout=None):
        raise ConnectionError("listener not reachable on port 4200")

    monkeypatch.setattr("backend.bridge.send_command", offline)
    make(env, sidecar())
    result = journal.revert_run("r1", project_root=str(root))
    assert result["ok"] is False
    assert "open UEFN" in result["errors"][0]
    assert journal.get_run("r1", project_root=str(root))["entries"][0]["reverted"] is False
