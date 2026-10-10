"""ProjectWriter pipeline against a temp project: atomic writes, CAS, policy, locks, fan-out."""

from __future__ import annotations

import multiprocessing
import os
import time
import threading
import traceback
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


def test_reconstructed_writer_checks_current_bytes_not_persisted_history(project: Path, tmp_path: Path) -> None:
    from backend.store import db
    from backend.workspace.journal import FileChangeJournal
    from backend.workspace.writer import content_hash

    storage = tmp_path / "ledger" / "recovery-project"
    journal = FileChangeJournal(lambda _root: storage)
    original = ProjectWriter.for_root(str(project), journal=journal)
    token = identity.bind(RunContext(run_id="recover-run", conv_id="recover-chat"))
    try:
        first = original.write_text("Content/Verse/a.verse", "recorded baseline\n")
    finally:
        identity.reset(token)
    history = journal.get_run("recover-run", project_root=str(project))
    del original, journal
    db.close_thread_connections()

    path = project / "Content" / "Verse" / "a.verse"
    external = b"intervening human content\r\nkeep this line\r\n"
    path.write_bytes(external)
    recovered_journal = FileChangeJournal(lambda _root: storage)
    observer = RecordingObserver()
    recovered = ProjectWriter.for_root(str(project), journal=recovered_journal, observers=[observer])
    assert recovered_journal.get_run("recover-run", project_root=str(project)) == history
    assert recovered_journal.entry_contents("recover-run", 1, project_root=str(project))["after"] == "recorded baseline\n"
    token = identity.bind(RunContext(run_id="recover-run", conv_id="recover-chat"))
    try:
        with pytest.raises(StaleWrite):
            recovered.write_text("Content/Verse/a.verse", "stale overwrite\n", expected_hash=first.after_hash)
        assert path.read_bytes() == external
        assert recovered_journal.get_run("recover-run", project_root=str(project)) == history
        assert observer.calls == []
        assert [p.name for p in path.parent.iterdir()] == ["a.verse"]
        # Recovery does not disable writes: an explicitly refreshed baseline works.
        current = path.read_text(encoding="utf-8")
        recovered.write_text("Content/Verse/a.verse", "fresh edit\n", expected_hash=content_hash(current))
    finally:
        identity.reset(token)
    assert path.read_text(encoding="utf-8") == "fresh edit\n"
    assert len(observer.calls) == 1
    assert len(recovered_journal.get_run("recover-run", project_root=str(project))["entries"]) == 2


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


def test_preflight_canonicalizes_without_writing_or_granting_future_permission(project: Path) -> None:
    from unittest.mock import Mock

    policy = DenyOutside("Content/Verse/Shop/")
    journal = RecordingJournal()
    observer = RecordingObserver()
    w = ProjectWriter.for_root(str(project), policies=[policy], journal=journal, observers=[observer])
    paths = ("Content/Verse/Shop/./a.verse", "Content/Verse/Shop/b.verse")
    assert w.preflight_paths("move", paths, tool="workspace_move_file").allow
    assert policy.requests[-1].paths == ("Content/Verse/Shop/a.verse", "Content/Verse/Shop/b.verse")
    assert journal.records == [] and observer.calls == []
    assert not (project / "Content/Verse/Shop").exists()
    # Preflight is not a capability or reservation: a later denial still wins.
    policy.allowed = "Content/Verse/Other/"
    perform = Mock()
    with pytest.raises(WriteDenied):
        w.path_op("move", paths[1], source=paths[0], perform=perform)
    perform.assert_not_called()
    assert journal.records == [] and observer.calls == []


@pytest.mark.parametrize("op,paths", [("teleport", ("Content/a.txt",)), ("move", ())])
def test_preflight_rejects_invalid_operation_or_empty_paths(project: Path, op, paths) -> None:
    with pytest.raises(ValueError):
        ProjectWriter.for_root(str(project)).preflight_paths(op, paths)


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
    assert errors == [], "\n".join("".join(traceback.format_exception(exc)) for exc in errors)
    final = (project / "Content" / "Verse" / "race.verse").read_text(encoding="utf-8")
    assert final in (a, b)
    assert len(obs.calls) == rounds * 2
    # Every observed "before" is a complete prior version, never a torn read.
    for record, _ in obs.calls:
        assert record.before in ("", a, b)


def _increment_process(root, barrier, results):
    """Independent writer; retry CAS refusals until 100 increments land."""
    from backend.workspace.paths import content_hash

    writer = ProjectWriter.for_root(root)
    target = Path(root) / "Content/Verse/counter.verse"
    original = writer._atomic_write

    def slow_write(*args, **kwargs):
        # Widen the compare/write race so the test fails without the OS lock.
        time.sleep(0.002)
        return original(*args, **kwargs)

    writer._atomic_write = slow_write
    refusals = []
    for index in range(100):
        before = target.read_text(encoding="utf-8")
        if index == 0:
            barrier.wait(timeout=15)
        for attempt in range(1000):
            try:
                writer.write_text("Content/Verse/counter.verse", str(int(before) + 1),
                                  expected_hash=content_hash(before))
                break
            except StaleWrite as exc:
                refusals.append(str(exc))
                before = target.read_text(encoding="utf-8")
        else:
            raise AssertionError("CAS retries exhausted")
    results.put(refusals)


def _hold_writer_process(root, acquired):
    writer = ProjectWriter.for_root(root)

    def hold(*args, **kwargs):
        acquired.set()
        time.sleep(120)

    writer._atomic_write = hold
    writer.write_text("Content/Verse/counter.verse", "never landed")


def _single_writer_process(root, started):
    started.set()
    ProjectWriter.for_root(root).write_text("Content/Verse/counter.verse", "recovered")


def test_two_processes_preserve_200_writes_and_explain_refusals(project):
    target = project / "Content/Verse/counter.verse"
    target.write_text("0", encoding="utf-8")
    ctx = multiprocessing.get_context("spawn")
    barrier, results = ctx.Barrier(2), ctx.Queue()
    workers = [ctx.Process(target=_increment_process, args=(str(project), barrier, results))
               for _ in range(2)]
    try:
        for worker in workers:
            worker.start()
        refusals = results.get(timeout=30) + results.get(timeout=30)
        for worker in workers:
            worker.join(timeout=10)
            assert worker.exitcode == 0
        assert target.read_text(encoding="utf-8") == "200"
        assert refusals  # The first reads deliberately share one baseline.
        assert all("changed on disk since it was read" in reason
                   and "expected" in reason and "found" in reason
                   and "Re-read the file" in reason for reason in refusals)
    finally:
        for worker in workers:
            if worker.is_alive():
                worker.kill()
            worker.join(timeout=5)
        results.close()


def test_killed_writer_releases_os_lock(project):
    ctx = multiprocessing.get_context("spawn")
    acquired, started = ctx.Event(), ctx.Event()
    holder = ctx.Process(target=_hold_writer_process, args=(str(project), acquired))
    waiter = ctx.Process(target=_single_writer_process, args=(str(project), started))
    holder.start()
    try:
        assert acquired.wait(timeout=15)
        waiter.start()
        assert started.wait(timeout=15)
        waiter.join(timeout=0.3)
        assert waiter.is_alive(), "second process bypassed the held write lock"
        holder.kill()
        holder.join(timeout=5)
        waiter.join(timeout=15)
        assert waiter.exitcode == 0
        assert (project / "Content/Verse/counter.verse").read_text(encoding="utf-8") == "recovered"
    finally:
        for worker in (holder, waiter):
            if worker.is_alive():
                worker.kill()
            if worker.pid is not None:
                worker.join(timeout=5)


@pytest.mark.skipif(os.name != "nt", reason="Windows replacement sharing semantics")
def test_atomic_write_waits_for_reader_without_releasing_cas_lock(project, monkeypatch):
    from backend.workspace import writer as writer_module

    target = project / "Content/Verse/shared.verse"
    target.write_text("old", encoding="utf-8")
    observer = RecordingObserver()
    writer = ProjectWriter.for_root(str(project), observers=[observer])
    expected = writer_module.content_hash("old")
    denied = threading.Event()
    original_replace = os.replace
    errors = []
    failures = []

    def replace(src, dst):
        try:
            return original_replace(src, dst)
        except PermissionError as exc:
            failures.append(exc.winerror)
            denied.set()
            raise

    monkeypatch.setattr(writer_module.os, "replace", replace)

    def write():
        try:
            writer.write_text("Content/Verse/shared.verse", "new", expected_hash=expected)
        except BaseException as exc:
            errors.append(exc)

    thread = threading.Thread(target=write)
    try:
        with target.open(encoding="utf-8") as reader:
            thread.start()
            assert denied.wait(5), "replacement never reached the held reader"
            assert thread.is_alive()
            assert reader.read() == "old"
            # Retrying replacement must not release the writer's CAS mutex.
            lock = writer._lock_for(os.path.normcase(os.path.realpath(target)))
            acquired = lock.acquire(blocking=False)
            if acquired:
                lock.release()
            assert not acquired
            assert observer.calls == []
    finally:
        thread.join(5)
    assert not thread.is_alive()
    assert errors == [], "\n".join("".join(traceback.format_exception(exc)) for exc in errors)
    assert failures and all(code in (5, 32, 33) for code in failures)
    assert target.read_text(encoding="utf-8") == "new"
    assert len(observer.calls) == 1
    assert not list(target.parent.glob(".*.tmp"))


@pytest.mark.skipif(os.name != "nt", reason="Windows replacement sharing semantics")
def test_atomic_write_persistent_reader_denial_preserves_original(project):
    target = project / "Content/Verse/shared.verse"
    target.write_text("old", encoding="utf-8")
    observer = RecordingObserver()
    writer = ProjectWriter.for_root(str(project), observers=[observer])
    with target.open(encoding="utf-8"):
        with pytest.raises(PermissionError):
            writer.write_text("Content/Verse/shared.verse", "new")
    assert target.read_text(encoding="utf-8") == "old"
    assert observer.calls == []
    assert not list(target.parent.glob(".*.tmp"))


def test_atomic_write_does_not_retry_unrelated_error(project, monkeypatch):
    from backend.workspace import writer as writer_module

    target = project / "Content/Verse/shared.verse"
    target.write_text("old", encoding="utf-8")
    calls = []

    def replace(src, dst):
        calls.append((src, dst))
        raise OSError(28, "disk full")

    monkeypatch.setattr(writer_module.os, "replace", replace)
    with pytest.raises(OSError, match="disk full"):
        ProjectWriter.for_root(str(project)).write_text("Content/Verse/shared.verse", "new")
    assert len(calls) == 1
    assert target.read_text(encoding="utf-8") == "old"
    assert not list(target.parent.glob(".*.tmp"))
