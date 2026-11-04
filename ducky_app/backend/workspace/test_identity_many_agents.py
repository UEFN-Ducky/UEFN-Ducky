"""Many agents writing at once: every journal row carries its own writer's chat id.

Nov 3 2026: with ``Documents/GitHub`` open, the Guards seat's writes in the nested
``GitHub/UEFN-Ducky-Release`` project were journaled in the GitHub ledger. The repo's own
ledger had no stamp, so the Chats seat's next shell call (file_changes) claimed those files
and commit_guard refused the Guards seat's own commits.
"""

from __future__ import annotations

import threading
from pathlib import Path

import pytest

from backend.bridge.shared_mcp import identity_from_payload
from backend.workspace import events, identity, runtime
from backend.workspace.journal import FileChangeJournal
from backend.workspace.writer import ProjectWriter

AGENTS = 20
WRITES = 5


@pytest.fixture
def nested(tmp_path: Path, monkeypatch):
    from backend.workspace import writer as writer_mod

    # Both are folder projects (plain files), like Documents/GitHub and the repo in it.
    monkeypatch.setattr(writer_mod, "_is_folder_project_root", lambda _root: True)
    outer = tmp_path / "GitHub"
    inner = outer / "Repo"
    (inner / "pkg").mkdir(parents=True)
    ledgers = tmp_path / "appdata" / "changesets"
    journal = FileChangeJournal(lambda root: ledgers / Path(root).name)
    # The open project is the outer folder; the repo is another Ducky project inside it.
    writer = ProjectWriter.for_root(str(outer), journal=journal, other_roots=lambda: [str(inner)])
    runtime.reset_for_tests(writer)
    events.reset_for_tests()
    yield outer, inner, journal, writer
    runtime.reset_for_tests(None)
    events.reset_for_tests()


def _agent(n: int):
    return identity_from_payload({
        "conv_id": f"conv-{n:02d}", "run_id": f"run-{n:02d}", "ducky_name": f"Seat {n}",
        "coding_agent": "claude_code",
    })


def test_a_nested_project_write_lands_in_that_projects_ledger(nested) -> None:
    outer, inner, journal, writer = nested
    token = identity.bind(_agent(1))
    try:
        res = writer.write_text("Repo/pkg/guard.txt", "x = 1\n", tool="workspace_write_file")
    finally:
        identity.reset(token)
    assert res.path == "pkg/guard.txt"
    stamp = journal.index_stamp("pkg/guard.txt", project_root=str(inner))
    assert stamp is not None and stamp["conv_id"] == "conv-01"
    # Not in the outer ledger, where the repo's git snapshot never looks.
    assert journal.index_stamp("Repo/pkg/guard.txt", project_root=str(outer)) is None


def test_twenty_agents_each_credited_with_their_own_writes(nested, monkeypatch) -> None:
    from frontend.ui_web.terminal import commit_guard

    outer, inner, journal, writer = nested
    errors: list[BaseException] = []
    start = threading.Barrier(AGENTS)

    def work(n: int) -> None:
        token = identity.bind(_agent(n))
        try:
            start.wait()
            for i in range(WRITES):
                writer.write_text(f"Repo/pkg/a{n:02d}_{i}.txt", f"# agent {n} write {i}\n", tool="workspace_write_file")
        except BaseException as exc:  # noqa: BLE001 - surfaced below
            errors.append(exc)
        finally:
            identity.reset(token)

    threads = [threading.Thread(target=work, args=(n,)) for n in range(AGENTS)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(60)
    assert not errors, errors

    credited = 0
    for n in range(AGENTS):
        for i in range(WRITES):
            stamp = journal.index_stamp(f"pkg/a{n:02d}_{i}.txt", project_root=str(inner)) or {}
            assert stamp.get("conv_id") == f"conv-{n:02d}", (n, i, stamp)
            assert stamp.get("run_id") == f"run-{n:02d}"
            credited += 1
        run = journal.get_run(f"run-{n:02d}", project_root=str(inner))
        assert run["conv_id"] == f"conv-{n:02d}" and len(run["entries"]) == WRITES
    assert credited == AGENTS * WRITES

    # commit_guard reads the innermost project's ledger: each agent may commit its own files.
    def stamp_writer(path: str) -> tuple[str, str]:
        rel = Path(path).resolve().relative_to(inner.resolve()).as_posix()
        stamp = journal.index_stamp(rel, project_root=str(inner)) or {}
        return str(stamp.get("conv_id") or ""), str(stamp.get("ducky_name") or "")

    for n in range(AGENTS):
        own = [f"pkg/a{n:02d}_{i}.txt" for i in range(WRITES)]
        monkeypatch.setattr(commit_guard, "to_commit", lambda _c, _w, own=own: (str(inner), own))
        assert commit_guard.check("git commit -m one", str(inner), f"conv-{n:02d}",
                                  writer=stamp_writer, live=lambda _c: True) == ""
        # And another agent's commit of those files is still refused.
        other = f"conv-{(n + 1) % AGENTS:02d}"
        assert "another live chat" in commit_guard.check("git commit -m one", str(inner), other,
                                                         writer=stamp_writer, live=lambda _c: True)
