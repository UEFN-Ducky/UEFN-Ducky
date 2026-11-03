"""Workflows owned by Local or a shared space, on one PC, with a fake spaces provider.

Checks: Local workflows are per account and sealed; signed-out workflows can be
brought into the account; old shared rows are claimed once with their versions; a
shared workflow lists under its space and queues for the provider's next round; a
space whose workflows this account may not change is read-only with the provider's
reason (it can still run them); "Run on this PC" gates shared schedules; background
rounds only for spaces this PC runs; access lost hides a space's workflows and the
provider may drop them.
"""

from __future__ import annotations

import sys
import threading
import time
from typing import Any

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="DPAPI")

from backend.automations import owned, scheduler, shared, store, versions
from backend.store import db
from backend.store.repos import plugin_data as repo
from backend.store.repos import workflows as runtime
from backend.uefn_plugins import scopes, spaces
from backend.uefn_plugins import test_scopes
from backend.uefn_plugins.test_scopes import FakeSpaces, _Who

# The account switcher and data keys of the plugin-data checks.
account_keys = test_scopes.account_keys
who = test_scopes.who

CRON = {"nodes": [{"id": "c", "type": "start.cron", "x": 0, "y": 0, "config": {"interval_seconds": 1}}], "edges": []}
NO_RIGHTS = "Only space editors can change these workflows."


class FakeWorkflowSpaces(FakeSpaces):
    def __init__(self) -> None:
        super().__init__()
        self.editors: dict[str, bool] = {}
        self.refreshed = 0

    def workflow_edit_refusal(self, account: str, space_id: str, label: str) -> str:
        return NO_RIGHTS

    def refresh_workflow_spaces(self) -> None:
        self.refreshed += 1
        account = scopes.account_id()
        for sid, label in self.spaces.items():
            repo.sync_put(account, sid, label=label, members=2)
            runtime.meta_put(account, sid, workflows_writable=self.editors.get(sid, True), web_slug=sid.lower())


@pytest.fixture
def fake(account_keys) -> FakeWorkflowSpaces:
    impl = FakeWorkflowSpaces()
    spaces.install_for_tests(impl)
    yield impl
    spaces.reset_for_tests()


@pytest.fixture(autouse=True)
def quiet(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(store, "_announce_graphs_changed", lambda: None)


def join(fake: FakeWorkflowSpaces, who: _Who, email: str, space: str = "spaceT", *, editor: bool = True) -> str:
    """Sign in as ``email`` with ``space`` on this PC, pulled once (its folder opens for writes)."""
    aid = who.be(email)
    fake.spaces[space] = "Alpha Studio" if space == "spaceT" else "Other Space"
    fake.editors[space] = editor
    shared.refresh_spaces()
    repo.sync_put(aid, space, synced_at=time.time())
    return aid


def owners_by_id() -> dict[str, dict[str, Any]]:
    return {o["id"]: o for o in shared.owners()["owners"]}


# --------------------------------------------------------------------------- Local


def test_local_workflows_are_per_account_and_sealed(who: _Who) -> None:
    ana = who.be("ana@x.org")
    wf = store.save_workflow({"name": "Ana's chat flow", "graph": {"nodes": [], "edges": []}})
    assert wf["owner"] == {"id": "local", "kind": "local", "label": "Local", "state": "ok", "readOnly": False,
                           "reason": ""}
    raw = repo.get(ana, scopes.PERSONAL, owned.DOC_PLUGIN, "doc", wf["id"])
    assert raw["value"].startswith("enc1:") and "Ana's" not in raw["value"] and raw["dirty"] == 0
    store.append_run(wf["id"], {"ok": True, "ended": 5, "steps": [{"label": "secret step"}]})
    stored_runs = runtime.runtime_get(ana, wf["id"])["runs"]
    assert stored_runs.startswith("enc1:") and "secret" not in stored_runs

    who.be("bo@x.org")
    assert store.get_workflow(wf["id"]) is None and store.list_workflows() == []
    assert versions.list_versions(wf["id"]) == []  # sealed for Ana
    who.be("")
    assert store.list_workflows() == []
    who.be("ana@x.org")
    assert [w["name"] for w in store.list_workflows()] == ["Ana's chat flow"]
    assert store.get_workflow(wf["id"])["runs"][0]["steps"] == [{"label": "secret step"}]
    assert store.clear_runs(wf["id"]) is True  # Run log -> Clear log
    assert store.get_workflow(wf["id"])["runs"] == [] and store.get_workflow(wf["id"])["name"] == "Ana's chat flow"
    assert store.clear_runs("missing") is False


def test_signed_out_workflows_can_be_brought_into_the_account(who: _Who) -> None:
    who.be("")
    wf = store.save_workflow({"name": "Made offline", "graph": CRON})
    store.append_run(wf["id"], {"ok": True, "ended": 9})
    assert shared.owners()["localImport"] == 0  # signed out: nothing to offer
    who.be("ana@x.org")
    assert store.list_workflows() == [] and shared.owners()["localImport"] == 1
    assert shared.import_local() == {"ok": True, "moved": 1}
    got = store.get_workflow(wf["id"])
    assert got["name"] == "Made offline" and got["owner"]["kind"] == "local" and got["runs"] == [{"ok": True, "ended": 9}]
    assert [v["name"] for v in versions.list_versions(wf["id"])] == ["Made offline"]
    assert shared.owners()["localImport"] == 0
    who.be("")
    assert store.list_workflows() == []


def test_legacy_shared_rows_are_claimed_once_with_their_versions(who: _Who) -> None:
    from backend.store.repos import automations as legacy

    legacy.put({"id": "old-flow", "name": "Before owners", "graph": CRON, "runs": [{"ok": True}], "last_run": 3.0})
    conn = db.connect()
    with db.write_txn(conn):
        legacy.archive(conn, "old-flow", 1.0, '{"id": "old-flow", "name": "Old copy", "graph": {}}')
    ana = who.be("ana@x.org")
    rows = store.list_workflows()
    assert [(r["name"], r["owner"]["kind"], r["last_run"]) for r in rows] == [("Before owners", "local", 3.0)]
    assert not legacy.has_legacy()
    stored = conn.execute("SELECT snapshot FROM workflow_versions WHERE workflow_id='old-flow'").fetchone()[0]
    assert stored.startswith("enc1:")
    assert [v["name"] for v in versions.list_versions("old-flow")] == ["Old copy"]
    assert store.get_workflow("old-flow")["runs"] == [{"ok": True}]
    who.be("bo@x.org")
    assert store.list_workflows() == []  # claimed by the first account only
    assert ana


# --------------------------------------------------------------------------- shared spaces


def test_without_a_provider_only_local_is_listed(who: _Who) -> None:
    who.be("ana@x.org")
    out = shared.refresh_spaces()
    assert [o["id"] for o in out["owners"]] == ["local"]
    assert out["spacesEnabled"] is False and out["kindLabel"] == "space"
    assert shared.sync(force=True) == {"ok": True, "started": False}


def test_owners_come_from_the_provider_with_its_names(who: _Who, fake: FakeWorkflowSpaces) -> None:
    join(fake, who, "ana@x.org")
    out = shared.refresh_spaces()
    assert fake.refreshed >= 2 and out["spacesEnabled"] is True and out["kindLabel"] == "space"
    owner = out["owners"][1]
    assert (owner["id"], owner["kind"], owner["label"], owner["kindLabel"], owner["pausedLabel"]) == (
        "spaceT", "shared", "Alpha Studio", "space", "Space paused")
    assert owner["slug"] == "spacet" and owner["sync"]["members"] == 2 and owner["readOnly"] is False


def test_shared_workflow_lists_under_its_space_and_only_its_account_sees_it(who: _Who, fake: FakeWorkflowSpaces) -> None:
    """Its sync rounds run in the team engine (Ducky Account) and are tested there."""
    join(fake, who, "ana@x.org")
    wf = store.save_workflow({"name": "Nightly build", "graph": CRON}, owner="spaceT")
    assert wf["owner"]["kind"] == "shared" and wf["owner"]["label"] == "Alpha Studio" and wf["run_here"] is True
    assert owners_by_id()["spaceT"]["sync"]["pending"] == 1
    assert shared.sync(space_id="spaceNope")["ok"] is False

    # Another account on this PC, and another space, never see it.
    join(fake, who, "cy@x.org", space="spaceU")
    assert store.get_workflow(wf["id"]) is None
    assert [o["id"] for o in shared.owners()["owners"]] == ["local", "spaceT", "spaceU"]
    assert [w["name"] for w in store.list_workflows()] == []


def test_shared_folders_live_with_the_space(who: _Who, fake: FakeWorkflowSpaces) -> None:
    join(fake, who, "ana@x.org")
    wf = store.save_workflow({"name": "Nightly build", "folder": "Builds", "graph": CRON}, owner="spaceT")
    assert store.move_folder("spaceT", "Builds", "CI/Builds") == 1
    assert store.move_folder("local", "CI", "Other") == 0  # Local has no such folder
    assert store.get_workflow(wf["id"])["folder"] == "CI/Builds"
    assert [r["folder"] for r in store.list_workflows()] == ["CI/Builds"]


def test_without_rights_a_member_can_run_but_not_change(who: _Who, fake: FakeWorkflowSpaces) -> None:
    from backend.automations.runner import run_workflow

    join(fake, who, "ana@x.org")
    wf = store.save_workflow({"name": "Shared", "graph": {"nodes": [
        {"id": "w", "type": "flow.wait", "x": 0, "y": 0, "config": {"seconds": 0}}], "edges": []}}, owner="spaceT")

    fake.editors["spaceT"] = False
    shared.refresh_spaces()
    owner = owners_by_id()["spaceT"]
    assert owner["readOnly"] is True and owner["reason"] == NO_RIGHTS
    with pytest.raises(PermissionError):
        store.save_workflow({"id": wf["id"], "name": "Rename"})
    with pytest.raises(PermissionError):
        store.save_workflow({"name": "New"}, owner="spaceT")
    with pytest.raises(PermissionError):
        store.delete_workflow(wf["id"])
    assert run_workflow(wf["id"])["ok"] is True
    assert store.get_workflow(wf["id"])["name"] == "Shared"


def test_paused_space_still_lists_and_takes_a_workflow(who: _Who, fake: FakeWorkflowSpaces) -> None:
    aid = join(fake, who, "ana@x.org")
    repo.sync_put(aid, "spaceT", state="paused")
    owner = owners_by_id()["spaceT"]
    assert owner["state"] == "paused" and owner["readOnly"] is False
    wf = store.save_workflow({"name": "While paused", "graph": CRON}, owner="spaceT")
    assert wf["owner"]["id"] == "spaceT" and wf["name"] == "While paused"


def _tick() -> None:
    for thread in scheduler._tick():  # each due workflow runs on its own thread
        thread.join(5)


def test_run_on_this_pc_gates_shared_schedules(who: _Who, fake: FakeWorkflowSpaces, monkeypatch) -> None:
    hits: list[str] = []
    monkeypatch.setattr(scheduler, "run_workflow", lambda wid, **k: hits.append(wid) or {"ok": True})
    monkeypatch.setattr(shared, "background", lambda *a, **k: [])
    ana = join(fake, who, "ana@x.org")
    wf = store.save_workflow({"name": "Every second", "graph": CRON}, owner="spaceT")
    _tick()
    assert hits == [wf["id"]]  # the PC that made it runs it
    # As it arrives on another member's PC: no per-PC row yet, so it doesn't run there.
    runtime.runtime_delete(ana, [wf["id"]])
    assert store.get_workflow(wf["id"])["run_here"] is False
    _tick()
    assert hits == [wf["id"]]
    store.set_run_here(wf["id"], True)
    _tick()
    assert hits == [wf["id"], wf["id"]]


def test_background_round_only_for_spaces_this_pc_runs(who: _Who, fake: FakeWorkflowSpaces, monkeypatch) -> None:
    rounds: list[list[tuple[str, str]]] = []
    done = threading.Event()
    monkeypatch.setattr(shared, "_round", lambda targets, force: rounds.append(targets) or done.set())
    monkeypatch.setattr(shared, "_LAST_BACKGROUND", {})
    ana = join(fake, who, "ana@x.org")
    store.save_workflow({"name": "Chat only", "graph": {"nodes": [], "edges": []}}, owner="spaceT")
    assert shared.background(store.all_workflows(), now=1000.0) == []
    store.save_workflow({"name": "Schedule", "graph": CRON}, owner="spaceT")
    assert shared.background(store.all_workflows(), now=1000.0) == ["spaceT"]
    assert done.wait(5) and rounds == [[(ana, "spaceT")]]
    assert shared.background(store.all_workflows(), now=1000.0 + 60) == []
    assert shared.background(store.all_workflows(), now=1000.0 + shared.BACKGROUND_EVERY_S) == ["spaceT"]


def test_copy_and_move_between_local_and_a_space(who: _Who, fake: FakeWorkflowSpaces) -> None:
    join(fake, who, "ana@x.org")
    mine = store.save_workflow({"name": "Mine", "graph": CRON})
    copy = store.copy_workflow(mine["id"], "spaceT")
    assert copy["id"] != mine["id"] and copy["owner"]["id"] == "spaceT" and copy["run_here"] is True
    moved = store.copy_workflow(mine["id"], "spaceT", move=True)
    assert moved["id"] == mine["id"] and moved["owner"]["id"] == "spaceT"
    back = store.copy_workflow(mine["id"], "local", move=True)
    assert back["owner"]["kind"] == "local"
    assert sorted(w["owner"]["id"] for w in store.list_workflows()) == ["local", "spaceT"]
    with pytest.raises(ValueError):
        store.copy_workflow(mine["id"], "spaceNope")


def test_a_lost_space_hides_its_workflows_until_access_returns(who: _Who, fake: FakeWorkflowSpaces) -> None:
    ana = join(fake, who, "ana@x.org")
    wf = store.save_workflow({"name": "Shared", "graph": CRON}, owner="spaceT")
    # Access lost: the space's workflows lock (not listed, never run) but stay on this PC.
    repo.sync_put(ana, "spaceT", state="lost")
    assert store.get_workflow(wf["id"]) is None
    assert [o["id"] for o in shared.owners()["owners"]] == ["local"]
    assert repo.rows(ana, "spaceT", owned.DOC_PLUGIN, "doc")
    # Back: they unlock as they were.
    repo.sync_put(ana, "spaceT", state="ok")
    assert store.get_workflow(wf["id"]) is not None
    # The provider drops the copy: the space's rows and per-PC state go.
    repo.sync_put(ana, "spaceT", state="lost")
    fake.expired.add("spaceT")
    assert [o["id"] for o in shared.owners()["owners"]] == ["local"]
    assert repo.rows(ana, "spaceT", owned.DOC_PLUGIN, "doc") == []
    assert runtime.runtime_get(ana, wf["id"])["run_here"] is False


def test_saving_while_the_space_is_locked_here_is_refused_never_forked_into_local(
    who: _Who, fake: FakeWorkflowSpaces
) -> None:
    ana = join(fake, who, "ana@x.org")
    wf = store.save_workflow({"name": "Shared", "graph": CRON}, owner="spaceT")
    repo.sync_put(ana, "spaceT", state="unavailable", error="permission denied")
    for owner in ("", "spaceT"):  # an old editor sends no owner; the editor now sends it
        with pytest.raises(owned.ReadOnlyOwner, match=r"Alpha Studio isn't available right now \(permission denied\)"):
            store.save_workflow({"id": wf["id"], "name": "Renamed"}, owner=owner)
    repo.sync_put(ana, "spaceT", state="lost")
    with pytest.raises(owned.ReadOnlyOwner, match="no longer have access to Alpha Studio"):
        store.save_workflow({"id": wf["id"], "name": "Renamed"})
    assert repo.get(ana, scopes.PERSONAL, owned.DOC_PLUGIN, "doc", wf["id"]) is None  # no Local fork
    # Back: the save lands in the space's copy.
    repo.sync_put(ana, "spaceT", state="ok", error="")
    assert store.save_workflow({"id": wf["id"], "name": "Renamed"})["owner"]["id"] == "spaceT"


def test_an_id_in_local_and_a_space_opens_the_space_copy(who: _Who, fake: FakeWorkflowSpaces) -> None:
    ana = join(fake, who, "ana@x.org")
    wf = store.save_workflow({"name": "Team copy", "graph": CRON}, owner="spaceT")
    # A Local copy under the same id, as an older app forked it while the space was away.
    owned.write(scopes.personal_scope(ana), {**store.get_workflow(wf["id"]), "name": "Old fork"})
    got = store.get_workflow(wf["id"])
    assert got["owner"]["id"] == "spaceT" and got["name"] == "Team copy"
    assert store.save_workflow({"id": wf["id"], "name": "Edited"})["owner"]["id"] == "spaceT"


def test_deleting_a_never_sent_shared_workflow_leaves_a_tombstone(who: _Who, fake: FakeWorkflowSpaces) -> None:
    ana = join(fake, who, "ana@x.org")
    wf = store.save_workflow({"name": "Shared", "graph": CRON}, owner="spaceT")
    assert repo.get(ana, "spaceT", owned.DOC_PLUGIN, "doc", wf["id"])["rev"] == 0
    assert store.delete_workflow(wf["id"]) is True
    row = repo.get(ana, "spaceT", owned.DOC_PLUGIN, "doc", wf["id"])
    assert row["deleted"] == 1 and row["dirty"] == 1  # the delete still goes out (a push may be in flight)


def test_a_local_fork_of_a_spaces_workflow_gets_its_own_id(who: _Who, fake: FakeWorkflowSpaces) -> None:
    ana = join(fake, who, "ana@x.org")
    wf = store.save_workflow({"name": "Team copy", "graph": CRON}, owner="spaceT")
    store.append_run(wf["id"], {"ok": True, "ended": 7})
    # A Local copy under the same id, as an older app forked it while the space was away.
    owned.write(scopes.personal_scope(ana), {**store.get_workflow(wf["id"]), "name": "Old fork"})
    # While one id names two workflows, a delete or a move by id alone guesses at nothing.
    with pytest.raises(ValueError, match="More than one copy"):
        store.delete_workflow(wf["id"])
    with pytest.raises(ValueError, match="More than one copy"):
        store.set_folder(wf["id"], "Elsewhere")
    with pytest.raises(ValueError, match="More than one copy"):
        store.copy_workflow(wf["id"], "local", move=True)
    # The next start splits it: the Local copy gets its own id, with its runs and History.
    owned.reset_for_tests()
    rows = {w["owner"]["id"]: w for w in store.list_workflows()}
    fork = rows["local"]
    assert fork["name"] == "Old fork" and fork["id"] != wf["id"] and rows["spaceT"]["id"] == wf["id"]
    assert repo.get(ana, scopes.PERSONAL, owned.DOC_PLUGIN, "doc", wf["id"]) is None
    assert store.get_workflow(fork["id"])["runs"] == [{"ok": True, "ended": 7}]
    assert [v["name"] for v in versions.list_versions(fork["id"])] == ["Team copy"]
    # Deleting the fork leaves the team's copy alone.
    assert store.delete_workflow(fork["id"]) is True
    team = repo.get(ana, "spaceT", owned.DOC_PLUGIN, "doc", wf["id"])
    assert team["deleted"] == 0 and store.get_workflow(wf["id"])["name"] == "Team copy"


def test_a_space_reset_on_the_server_is_read_only_until_upload_or_clear(who: _Who, fake: FakeWorkflowSpaces) -> None:
    ana = join(fake, who, "ana@x.org")
    wf = store.save_workflow({"name": "Team flow", "graph": CRON}, owner="spaceT")
    for row in repo.dirty(ana, "spaceT", 100):
        repo.mark_pushed(ana, "spaceT", row["plugin_id"], row["kind"], row["key"], rev=4, sha256=row["sha256"])
    repo.sync_put(ana, "spaceT", state="reset", error="This team's data was reset on the server.")
    owner = owners_by_id()["spaceT"]
    assert owner["readOnly"] is True and "reset on the server" in owner["reason"]
    assert owner["resetContents"] == ["Workflows"]
    with pytest.raises(owned.ReadOnlyOwner, match="reset on the server"):
        store.save_workflow({"id": wf["id"], "name": "Edited"})
    # Update online leaves the copy as it is until the choice.
    shared.sync(force=True, space_id="spaceT", upload=True)
    assert repo.get(ana, "spaceT", owned.DOC_PLUGIN, "doc", wf["id"])["dirty"] == 0
    # A space holding only workflows has its way out too: Upload my copy.
    assert spaces.settle_reset("spaceT", "upload")["ok"] is True
    row = repo.get(ana, "spaceT", owned.DOC_PLUGIN, "doc", wf["id"])
    assert (row["rev"], row["dirty"]) == (0, 1) and repo.sync_get(ana, "spaceT")["state"] == "ok"
    assert store.save_workflow({"id": wf["id"], "name": "Edited"})["owner"]["id"] == "spaceT"
    # Or Clear my copy: the space's workflows and their per-PC state go, and nothing is sent.
    repo.sync_put(ana, "spaceT", state="reset")
    assert spaces.settle_reset("spaceT", "clear") == {"ok": True, "plugins": [owned.DOC_PLUGIN], "cleared": 1}
    assert repo.rows(ana, "spaceT", owned.DOC_PLUGIN, "doc") == [] and repo.count_dirty(ana, "spaceT") == 0
    assert runtime.runtime_get(ana, wf["id"])["run_here"] is False
    assert owners_by_id()["spaceT"]["readOnly"] is False


def test_a_shared_workflow_must_fit_once_sealed(who: _Who, fake: FakeWorkflowSpaces, monkeypatch) -> None:
    ana = join(fake, who, "ana@x.org")
    wf = store.save_workflow({"name": "Big", "graph": CRON})
    size = int(repo.get(ana, scopes.PERSONAL, owned.DOC_PLUGIN, "doc", wf["id"])["size"])
    monkeypatch.setattr(scopes, "DOC_MAX_BYTES", size + 10)  # fits Local, not once sealed for a space
    assert store.save_workflow({"id": wf["id"], "name": "Big", "description": "ok"})["owner"]["kind"] == "local"
    with pytest.raises(ValueError, match="too big to share"):
        store.copy_workflow(wf["id"], "spaceT")
    assert repo.rows(ana, "spaceT", owned.DOC_PLUGIN, "doc") == []
    monkeypatch.setattr(scopes, "DOC_MAX_BYTES", 200)
    owned.write_folders(owned.scope_for("local"), ["f" * 180])  # 195 bytes encoded
    with pytest.raises(ValueError, match="too big to share"):
        owned.write_folders(owned.scope_for("spaceT"), ["f" * 180])
