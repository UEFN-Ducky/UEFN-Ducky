"""ProjectWriter pipeline against a temp project: atomic writes, CAS, policy, locks, fan-out."""

from __future__ import annotations

import os
import threading
from pathlib import Path
from typing import Any

import pytest

from backend.workspace import events, identity
from backend.workspace.identity import RunContext
from backend.workspace.policy import Decision, WriteRequest
from backend.workspace.writer import ProjectWriter, StaleWrite, WriteDenied, WriteRecord, WriteResult


class RecordingObserver:
    def __init__(self) -> None:
        self.calls: list[tuple[WriteRecord, WriteResult]] = []

    def on_write(self, record: WriteRecord, result: WriteResult) -> None:
        self.calls.append((record, result))


class RecordingJournal:
    def __init__(self, *, fail: bool = False) -> None:
        self.records: list[WriteRecord] = []
        self.reads: list[tuple[str, str]] = []
        self.fail = fail

    def record(self, record: WriteRecord) -> dict[str, Any]:
        if self.fail:
            raise OSError("disk full")
        self.records.append(record)
        return {"run_id": record.writer.get("run_id", ""), "seq": len(self.records), "warning": ""}

    def note_read(self, path: str, content_hash: str, project_root: str = "") -> None:
        self.reads.append((path, content_hash))


class DenyOutside:
    """Toy lane policy: only paths under `allowed` may be written."""

    name = "lane"

    def __init__(self, allowed: str, *, shadow: bool = False) -> None:
        self.allowed = allowed
        self.shadow = shadow
        self.requests: list[WriteRequest] = []

    def check(self, request: WriteRequest) -> Decision:
        self.requests.append(request)
        bad = [p for p in request.paths if not p.startswith(self.allowed)]
        if not bad:
            return Decision(policy=self.name)
        if self.shadow:
            return Decision(policy=self.name, shadow_violation=True, reason=f"out of lane: {bad[0]}")
        return Decision(
            allow=False,
            policy=self.name,
            reason=f"out of lane: {bad[0]}",
            hint="ask the leader",
            details={"kind": "lane_denied"},
        )


@pytest.fixture
def project(tmp_path: Path) -> Path:
    root = tmp_path / "Proj"
    (root / "Content" / "Verse").mkdir(parents=True)
    return root


@pytest.fixture(autouse=True)
def _clean_sinks():
    events.reset_for_tests()
    yield
    events.reset_for_tests()


def test_write_text_creates_file_and_reports_delta(project: Path) -> None:
    w = ProjectWriter.for_root(str(project))
    res = w.write_text("Content/Verse/a.verse", "one\ntwo\n", tool="t")
    assert (project / "Content" / "Verse" / "a.verse").read_text(encoding="utf-8") == "one\ntwo\n"
    assert res.path == "Content/Verse/a.verse"
    assert res.before_hash == "" and len(res.after_hash) == 16
    assert (res.lines_added, res.lines_removed) == (2, 0)
    assert res.in_lane is None
    payload = res.to_payload()
    assert payload["relative_path"] == "Content/Verse/a.verse" and payload["bytes_written"] == 8


def test_write_text_overwrites_and_keeps_before(project: Path) -> None:
    w = ProjectWriter.for_root(str(project))
    w.write_text("Content/Verse/a.verse", "one\n")
    res = w.write_text("Content/Verse/a.verse", "uno\n")
    assert res.before_content == "one\n"
    assert (res.lines_added, res.lines_removed) == (1, 1)


def test_no_temp_files_left_behind(project: Path) -> None:
    w = ProjectWriter.for_root(str(project))
    w.write_text("Content/Verse/a.verse", "x")
    assert sorted(p.name for p in (project / "Content" / "Verse").iterdir()) == ["a.verse"]


def test_create_refuses_existing(project: Path) -> None:
    w = ProjectWriter.for_root(str(project))
    w.create("Content/Verse/a.verse", "x")
    with pytest.raises(ValueError, match="Already exists"):
        w.create("Content/Verse/a.verse", "y")


def test_expected_hash_mismatch_leaves_file_untouched(project: Path) -> None:
    w = ProjectWriter.for_root(str(project))
    first = w.write_text("Content/Verse/a.verse", "one\n")
    w.write_text("Content/Verse/a.verse", "two\n")
    with pytest.raises(StaleWrite):
        w.write_text("Content/Verse/a.verse", "three\n", expected_hash=first.after_hash)
    assert (project / "Content" / "Verse" / "a.verse").read_text(encoding="utf-8") == "two\n"


def test_expected_hash_matches_allows_write(project: Path) -> None:
    w = ProjectWriter.for_root(str(project))
    first = w.write_text("Content/Verse/a.verse", "one\n")
    w.write_text("Content/Verse/a.verse", "two\n", expected_hash=first.after_hash)


@pytest.mark.parametrize(
    "rel",
    ["Saved/x.txt", "Proj.uproject", "Content/Python/x.py", "Content/Verse/Fortnite.digest.verse", "../escape.txt"],
)
def test_guarded_paths_are_refused_before_write(project: Path, rel: str) -> None:
    w = ProjectWriter.for_root(str(project))
    with pytest.raises(ValueError):
        w.write_text(rel, "x")
    assert not (project / rel).exists()


def test_policy_denial_raises_and_emits_event(project: Path) -> None:
    seen: list[dict] = []
    events.register_sink(seen.append)
    policy = DenyOutside("Content/Verse/Shop/")
    w = ProjectWriter.for_root(str(project), policies=[policy])
    token = identity.bind(RunContext(run_id="r1", conv_id="c1", lane=("Content/Verse/Shop/**",)))
    try:
        with pytest.raises(WriteDenied) as exc:
            w.write_text("Content/Verse/Hub/hub.verse", "x", tool="workspace_write_file")
    finally:
        identity.reset(token)
    assert exc.value.hint == "ask the leader"
    assert not (project / "Content" / "Verse" / "Hub").exists()
    assert seen and seen[0]["type"] == "file_guard" and seen[0]["kind"] == "lane_denied"
    assert seen[0]["conv_id"] == "c1" and seen[0]["lane"] == ["Content/Verse/Shop/**"]
    assert policy.requests[0].ctx is not None and policy.requests[0].ctx.run_id == "r1"


def test_policy_allow_marks_in_lane_true(project: Path) -> None:
    w = ProjectWriter.for_root(str(project), policies=[DenyOutside("Content/Verse/Shop/")])
    res = w.write_text("Content/Verse/Shop/s.verse", "x")
    assert res.in_lane is True


def test_shadow_violation_writes_and_flags(project: Path) -> None:
    seen: list[dict] = []
    events.register_sink(seen.append)
    w = ProjectWriter.for_root(str(project), policies=[DenyOutside("Content/Verse/Shop/", shadow=True)])
    res = w.write_text("Content/Verse/Hub/h.verse", "x")
    assert (project / "Content" / "Verse" / "Hub" / "h.verse").is_file()
    assert res.in_lane is False
    assert seen and seen[0]["kind"] == "shadow_violation"


def test_observers_and_journal_receive_record(project: Path) -> None:
    obs = RecordingObserver()
    journal = RecordingJournal()
    w = ProjectWriter.for_root(str(project), observers=[obs], journal=journal)
    token = identity.bind(RunContext(run_id="r9", conv_id="c9", ducky_name="Hacker"))
    try:
        res = w.write_text("Content/Verse/a.verse", "x\n", tool="workspace_write_file")
    finally:
        identity.reset(token)
    assert len(obs.calls) == 1 and len(journal.records) == 1
    record, result = obs.calls[0]
    assert record.writer["ducky_name"] == "Hacker" and record.writer["tool"] == "workspace_write_file"
    assert record.writer["source"] == "agent"
    assert result is res and res.changeset == {"run_id": "r9", "seq": 1}


def test_explicit_writer_overrides_identity(project: Path) -> None:
    obs = RecordingObserver()
    w = ProjectWriter.for_root(str(project), observers=[obs])
    w.write_text("Content/Verse/a.verse", "x", writer={"source": "revert", "run_id": "revert:1"})
    assert obs.calls[0][0].writer["source"] == "revert"


def test_user_identity_when_nothing_bound(project: Path, monkeypatch) -> None:
    for key in RunContext().to_env():
        monkeypatch.delenv(key, raising=False)
    obs = RecordingObserver()
    w = ProjectWriter.for_root(str(project), observers=[obs])
    w.write_text("Content/Verse/a.verse", "x", tool="panel")
    assert obs.calls[0][0].writer["source"] == "user"


def test_journal_failure_becomes_warning_not_exception(project: Path) -> None:
    w = ProjectWriter.for_root(str(project), journal=RecordingJournal(fail=True))
    res = w.write_text("Content/Verse/a.verse", "x")
    assert (project / "Content" / "Verse" / "a.verse").is_file()
    assert "not ledgered" in res.warning


def test_observer_failure_does_not_break_write(project: Path) -> None:
    class Boom:
        def on_write(self, record: WriteRecord, result: WriteResult) -> None:
            raise RuntimeError("ui exploded")

    other = RecordingObserver()
    w = ProjectWriter.for_root(str(project), observers=[Boom(), other])
    w.write_text("Content/Verse/a.verse", "x")
    assert len(other.calls) == 1


def test_note_read_forwards_hash_to_journal(project: Path) -> None:
    journal = RecordingJournal()
    w = ProjectWriter.for_root(str(project), journal=journal)
    w.note_read("Content\\Verse\\a.verse", "abc")
    assert journal.reads == [("Content/Verse/a.verse", identity_hash("abc"))]


def identity_hash(text: str) -> str:
    from backend.workspace.paths import content_hash

    return content_hash(text)


def test_path_op_delete_records_before_and_trash_token(project: Path) -> None:
    obs = RecordingObserver()
    w = ProjectWriter.for_root(str(project), observers=[obs])
    target = project / "Content" / "Verse" / "a.verse"
    target.write_text("gone\n", encoding="utf-8")

    def perform() -> dict:
        target.unlink()
        return {"trash_token": "tok123"}

    res = w.path_op("delete", "Content/Verse/a.verse", tool="delete_project_entry", perform=perform)
    assert not target.exists()
    assert res.trash_token == "tok123" and res.before_content == "gone\n"
    record = obs.calls[0][0]
    assert record.op == "delete" and record.before == "gone\n" and record.lines_removed == 1


def test_path_op_move_checks_both_paths_and_records_from_path(project: Path) -> None:
    policy = DenyOutside("Content/Verse/Shop/")
    w = ProjectWriter.for_root(str(project), policies=[policy])
    src = project / "Content" / "Verse" / "Shop" / "a.verse"
    src.parent.mkdir(parents=True)
    src.write_text("x", encoding="utf-8")
    with pytest.raises(WriteDenied):
        w.path_op("move", "Content/Verse/Hub/a.verse", source="Content/Verse/Shop/a.verse", perform=lambda: None)
    assert src.exists()
    assert policy.requests[-1].paths == ("Content/Verse/Shop/a.verse", "Content/Verse/Hub/a.verse")

    def perform() -> None:
        dest = project / "Content" / "Verse" / "Shop" / "b.verse"
        os.replace(src, dest)

    res = w.path_op("move", "Content/Verse/Shop/b.verse", source="Content/Verse/Shop/a.verse", perform=perform)
    assert res.path == "Content/Verse/Shop/b.verse" and res.from_path == "Content/Verse/Shop/a.verse"


def test_path_op_copy_checks_only_destination(project: Path) -> None:
    policy = DenyOutside("Content/Verse/Shop/")
    w = ProjectWriter.for_root(str(project), policies=[policy])
    res = w.path_op("copy", "Content/Verse/Shop/c.verse", source="Content/Verse/Hub/a.verse", perform=lambda: None)
    assert policy.requests[-1].paths == ("Content/Verse/Shop/c.verse",)
    assert res.from_path == "Content/Verse/Hub/a.verse"
    with pytest.raises(WriteDenied):
        w.path_op("import", "Content/Verse/Hub/x.verse", perform=lambda: None)


def test_path_op_argument_validation(project: Path) -> None:
    w = ProjectWriter.for_root(str(project))
    with pytest.raises(ValueError):
        w.path_op("delete", "Content/Verse/a.verse", source="Content/Verse/b.verse", perform=lambda: None)
    with pytest.raises(ValueError):
        w.path_op("move", "Content/Verse/a.verse", perform=lambda: None)
    with pytest.raises(ValueError):
        w.path_op("teleport", "Content/Verse/a.verse", perform=lambda: None)


def test_concurrent_writes_to_one_path_never_interleave(project: Path) -> None:
    obs = RecordingObserver()
    w = ProjectWriter.for_root(str(project), observers=[obs])
    a = "A" * 20000 + "\n"
    b = "B" * 20000 + "\n"
    rounds = 40
    errors: list[BaseException] = []

    def worker(text: str) -> None:
        try:
            for _ in range(rounds):
                w.write_text("Content/Verse/race.verse", text)
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(a,)), threading.Thread(target=worker, args=(b,))]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []
    final = (project / "Content" / "Verse" / "race.verse").read_text(encoding="utf-8")
    assert final in (a, b)
    assert len(obs.calls) == rounds * 2
    # Every observed "before" is a complete prior version, never a torn read.
    for record, _ in obs.calls:
        assert record.before in ("", a, b)
