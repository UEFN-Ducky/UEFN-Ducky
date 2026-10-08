"""Workflows owned by Local or a team (DuckyOS plan §14), against a fake Store.

Checks: Local workflows are per account and sealed; a team workflow reaches every
member and never another team; without Manage automations a member can run but
not change it (and the Store refuses a forced push); "Run on this PC" gates team
schedules so one fires once, not once per member; old shared rows are claimed
once with their versions; signed-out workflows can be brought into the account;
a stale push keeps this PC's edit in History; leaving a team locks its folder and
a week later drops it.
"""

from __future__ import annotations

import sys
import threading
import time
from typing import Any

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="DPAPI")

from backend.automations import owned, scheduler, store, team, versions
from backend.store import db
from backend.store.repos import plugin_data as repo
from backend.store.repos import workflows as runtime
from backend.uefn_plugins import scopes, team_sync
from backend.uefn_plugins import test_scopes
from backend.uefn_plugins.test_scopes import FakeStore, _Who

# The fake Store, account switcher and data keys of the plugin-data checks.
account_keys = test_scopes.account_keys
fake_store = test_scopes.store
who = test_scopes.who

CRON = {"nodes": [{"id": "c", "type": "start.cron", "x": 0, "y": 0, "config": {"interval_seconds": 1}}], "edges": []}


@pytest.fixture(autouse=True)
def teams_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(team_sync, "teams_enabled", lambda: True)
    monkeypatch.setattr(store, "_announce_graphs_changed", lambda: None)


class Guarded:
    """The Store's rule for reserved ``ducky.*`` ids: pushes need Manage automations."""

    def __init__(self, inner: Any, may_automate: bool) -> None:
        self.inner, self.may = inner, may_automate

    def collect(self, event: str, body: dict[str, Any]) -> dict[str, Any]:
        pushes = body.get("pushes") or []
        refused = [p for p in pushes if not self.may and p["pluginId"].startswith("ducky.")]
        out = self.inner.collect(event, {**body, "pushes": [p for p in pushes if p not in refused]} if pushes else body)
        if refused:
            out["refused"] = [{"kind": p["kind"], "pluginId": p["pluginId"], "key": p["key"],
                               "error": "Only members with Manage automations can change team automations."}
                              for p in refused]
        return out

    def put(self, url: str, data: bytes) -> None:
        self.inner.put(url, data)

    def get(self, url: str) -> bytes:
        return self.inner.get(url)


def join(fake: FakeStore, who: _Who, email: str, team: str = "teamT", *, manage: bool = True) -> str:
    aid = who.be(email)
    fake.members.setdefault(team, set()).add(aid)
    repo.sync_put(aid, team, label="Alpha Studio" if team == "teamT" else "Other Team", members=2)
    runtime.perms_put(aid, team, manage_automations=manage)
    sync(fake, aid, team, manage=manage)  # first pull: the team folder opens for writes
    return aid


def sync(fake: FakeStore, aid: str, team: str = "teamT", *, manage: bool = True) -> dict[str, Any]:
    return team_sync.sync_team(aid, team, force=True, transport=Guarded(fake.transport(aid), manage))


def owners_by_kind() -> dict[str, dict[str, Any]]:
    return {o["id"]: o for o in team.owners()["owners"]}


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
    assert team.owners()["localImport"] == 0  # signed out: nothing to offer
    who.be("ana@x.org")
    assert store.list_workflows() == [] and team.owners()["localImport"] == 1
    assert team.import_local() == {"ok": True, "moved": 1}
    got = store.get_workflow(wf["id"])
    assert got["name"] == "Made offline" and got["owner"]["kind"] == "local" and got["runs"] == [{"ok": True, "ended": 9}]
    assert [v["name"] for v in versions.list_versions(wf["id"])] == ["Made offline"]
    assert team.owners()["localImport"] == 0
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


# --------------------------------------------------------------------------- Team


def test_team_workflow_reaches_members_and_never_another_team(who: _Who, fake_store: FakeStore) -> None:
    ana = join(fake_store, who, "ana@x.org")
    assert owners_by_kind()["teamT"]["readOnly"] is False
    wf = store.save_workflow({"name": "Nightly build", "graph": CRON}, owner="teamT")
    assert wf["owner"]["kind"] == "team" and wf["owner"]["label"] == "Alpha Studio" and wf["run_here"] is True
    assert team.owners()["owners"][1]["sync"]["pending"] == 1
    assert sync(fake_store, ana)["state"] == "ok"
    assert repo.count_dirty(ana, "teamT") == 0

    join(fake_store, who, "bo@x.org")
    got = store.get_workflow(wf["id"])
    assert got["name"] == "Nightly build" and got["owner"]["id"] == "teamT"
    assert got["run_here"] is False  # only the member who made it runs it by default

    join(fake_store, who, "cy@x.org", team="teamU")
    assert store.get_workflow(wf["id"]) is None
    assert [o["id"] for o in team.owners()["owners"]] == ["local", "teamU"]


def test_update_online_pushes_a_workflow_that_was_not_queued(who: _Who, fake_store: FakeStore, monkeypatch) -> None:
    ana = join(fake_store, who, "ana@x.org")
    wf = store.save_workflow({"name": "Prompt to picture", "graph": CRON}, owner="teamT")
    repo.clear_dirty(ana, "teamT", owned.DOC_PLUGIN, "doc", wf["id"])
    assert repo.count_dirty(ana, "teamT") == 0

    real = team_sync.sync_team

    def through_fake(account: str, team_key: str, *, force: bool = False, transport=None, now=None) -> dict[str, Any]:
        return real(account, team_key, force=True, transport=Guarded(fake_store.transport(account), True))

    monkeypatch.setattr(team_sync, "sync_team", through_fake)
    out = team.sync(force=True, team_id="teamT", upload=True)
    assert out["ok"] is True and repo.count_dirty(ana, "teamT") == 0
    join(fake_store, who, "bo@x.org")
    assert store.get_workflow(wf["id"])["name"] == "Prompt to picture"


def test_team_folders_reach_every_member(who: _Who, fake_store: FakeStore) -> None:
    ana = join(fake_store, who, "ana@x.org")
    wf = store.save_workflow({"name": "Nightly build", "folder": "Builds", "graph": CRON}, owner="teamT")
    assert store.move_folder("teamT", "Builds", "CI/Builds") == 1
    assert store.move_folder("local", "CI", "Other") == 0  # Local has no such folder
    sync(fake_store, ana)
    join(fake_store, who, "bo@x.org")
    assert store.get_workflow(wf["id"])["folder"] == "CI/Builds"
    assert [r["folder"] for r in store.list_workflows()] == ["CI/Builds"]


def test_member_without_manage_automations_can_run_but_not_change(who: _Who, fake_store: FakeStore, monkeypatch) -> None:
    ana = join(fake_store, who, "ana@x.org")
    wf = store.save_workflow({"name": "Shared", "graph": {"nodes": [
        {"id": "w", "type": "flow.wait", "x": 0, "y": 0, "config": {"seconds": 0}}], "edges": []}}, owner="teamT")
    sync(fake_store, ana)

    bo = join(fake_store, who, "bo@x.org", manage=False)
    owner = owners_by_kind()["teamT"]
    assert owner["readOnly"] is True and "Manage automations" in owner["reason"]
    with pytest.raises(PermissionError):
        store.save_workflow({"id": wf["id"], "name": "Bo's rename"})
    with pytest.raises(PermissionError):
        store.save_workflow({"name": "New"}, owner="teamT")
    with pytest.raises(PermissionError):
        store.delete_workflow(wf["id"])
    from backend.automations.runner import run_workflow

    assert run_workflow(wf["id"])["ok"] is True
    # A modified client that writes anyway is refused by the Store: the team copy stays.
    owned.write(scopes.team_scope(bo, "teamT"), {**store.get_workflow(wf["id"]), "name": "Forced"})
    out = sync(fake_store, bo, manage=False)
    assert "Manage automations" in out["error"]
    who.be("ana@x.org")
    sync(fake_store, ana)
    assert store.get_workflow(wf["id"])["name"] == "Shared"


def test_paused_team_still_lists_and_takes_a_workflow(who: _Who, fake_store: FakeStore) -> None:
    aid = join(fake_store, who, "ana@x.org")
    repo.sync_put(aid, "teamT", state="paused")
    owner = owners_by_kind()["teamT"]
    assert owner["state"] == "paused" and owner["readOnly"] is False
    wf = store.save_workflow({"name": "While paused", "graph": CRON}, owner="teamT")
    assert wf["owner"]["id"] == "teamT" and wf["name"] == "While paused"


def _tick() -> None:
    for thread in scheduler._tick():  # each due workflow runs on its own thread
        thread.join(5)


def test_run_on_this_pc_gates_team_schedules(who: _Who, fake_store: FakeStore, monkeypatch) -> None:
    hits: list[tuple[str, str]] = []
    monkeypatch.setattr(scheduler, "run_workflow", lambda wid, **k: hits.append((who.account, wid)) or {"ok": True})
    monkeypatch.setattr(team, "background", lambda *a, **k: [])
    ana = join(fake_store, who, "ana@x.org")
    wf = store.save_workflow({"name": "Every second", "graph": CRON}, owner="teamT")
    sync(fake_store, ana)
    _tick()
    join(fake_store, who, "bo@x.org")
    _tick()
    assert [h[1] for h in hits] == [wf["id"]]  # Ana's PC only
    store.set_run_here(wf["id"], True)
    _tick()
    assert len(hits) == 2 and "bo@x.org" in hits[1][0]


def test_background_round_only_for_teams_this_pc_runs(who: _Who, fake_store: FakeStore, monkeypatch) -> None:
    rounds: list[list[tuple[str, str]]] = []
    done = threading.Event()
    monkeypatch.setattr(team, "_round", lambda targets, force: rounds.append(targets) or done.set())
    monkeypatch.setattr(team, "_LAST_BACKGROUND", {})
    ana = join(fake_store, who, "ana@x.org")
    store.save_workflow({"name": "Chat only", "graph": {"nodes": [], "edges": []}}, owner="teamT")
    assert team.background(store.all_workflows(), now=1000.0) == []
    store.save_workflow({"name": "Schedule", "graph": CRON}, owner="teamT")
    assert team.background(store.all_workflows(), now=1000.0) == ["teamT"]
    assert done.wait(5) and rounds == [[(ana, "teamT")]]
    assert team.background(store.all_workflows(), now=1000.0 + 60) == []
    assert team.background(store.all_workflows(), now=1000.0 + team.BACKGROUND_EVERY_S) == ["teamT"]


def test_stale_push_keeps_this_pcs_edit_in_history(who: _Who, fake_store: FakeStore) -> None:
    ana = join(fake_store, who, "ana@x.org")
    wf = store.save_workflow({"name": "Base", "graph": CRON}, owner="teamT")
    sync(fake_store, ana)
    bo = join(fake_store, who, "bo@x.org")
    store.save_workflow({"id": wf["id"], "name": "Bo's edit"})
    sync(fake_store, bo)
    who.be("ana@x.org")
    store.save_workflow({"id": wf["id"], "name": "Ana's edit"})
    sync(fake_store, ana)  # stale: the server copy wins
    assert store.get_workflow(wf["id"])["name"] == "Bo's edit"
    rows = versions.list_versions(wf["id"])
    kept = next(v for v in rows if v["note"] == owned.BEFORE_SYNC_NOTE)
    assert kept["name"] == "Ana's edit"


def test_copy_and_move_between_local_and_a_team(who: _Who, fake_store: FakeStore) -> None:
    join(fake_store, who, "ana@x.org")
    mine = store.save_workflow({"name": "Mine", "graph": CRON})
    shared = store.copy_workflow(mine["id"], "teamT")
    assert shared["id"] != mine["id"] and shared["owner"]["id"] == "teamT" and shared["run_here"] is True
    moved = store.copy_workflow(mine["id"], "teamT", move=True)
    assert moved["id"] == mine["id"] and moved["owner"]["id"] == "teamT"
    back = store.copy_workflow(mine["id"], "local", move=True)
    assert back["owner"]["kind"] == "local"
    assert sorted(w["owner"]["id"] for w in store.list_workflows()) == ["local", "teamT"]
    with pytest.raises(ValueError):
        store.copy_workflow(mine["id"], "teamNope")


def test_leaving_a_team_locks_its_folder_and_a_week_later_drops_it(who: _Who, fake_store: FakeStore) -> None:
    ana = join(fake_store, who, "ana@x.org")
    wf = store.save_workflow({"name": "Team", "graph": CRON}, owner="teamT")
    sync(fake_store, ana)
    fake_store.members["teamT"].discard(ana)
    # Access lost: the team's workflows lock (not listed, never run) but stay on this PC.
    assert sync(fake_store, ana)["state"] == "lost"
    assert store.get_workflow(wf["id"]) is None
    assert [o["id"] for o in team.owners()["owners"]] == ["local"]
    assert repo.rows(ana, "teamT", team_sync.WORKFLOW_DOCS, "doc")
    # Back on the team: they unlock as they were.
    fake_store.members["teamT"].add(ana)
    assert sync(fake_store, ana)["state"] == "ok"
    assert store.get_workflow(wf["id"]) is not None
    # A week without access: the copy on this PC goes, per-PC state too.
    fake_store.members["teamT"].discard(ana)
    assert sync(fake_store, ana)["state"] == "lost"
    scopes.clear_lost(ana, "teamT")
    scopes.mark_lost(ana, "teamT", since=time.time() - scopes.LOST_KEEP_S - 60)
    assert sync(fake_store, ana)["state"] == "removed"
    assert repo.rows(ana, "teamT", team_sync.WORKFLOW_DOCS, "doc") == []
    assert runtime.runtime_get(ana, wf["id"])["run_here"] is False


def test_workflow_folders_list_every_team_even_without_team_private(who: _Who, monkeypatch: pytest.MonkeyPatch) -> None:
    from frontend import duckyos_account

    monkeypatch.setattr(team_sync, "teams_enabled", lambda: False)
    who.be("ana@x.org")
    monkeypatch.setattr(duckyos_account, "teams_snapshot", lambda **_: {
        "ok": True,
        "teams": [{
            "id": "teamT", "name": "tst", "slug": "tst", "members": [{}],
            "private_plan": None, "perms": {"manage_automations": True},
        }],
    })
    labels = [o["label"] for o in team.refresh_teams()["owners"]]
    assert labels == ["Local", "tst"]
    assert [c["id"] for c in team_sync.scope_choices()["choices"]] == ["personal"]
