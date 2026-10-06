"""Team folders against a fake Store: whole folder trees copied or moved to and from a
team keep their nesting and working Run workflow steps, and a team's folder list
(empty folders, renames) reaches every member."""

from __future__ import annotations

import sys

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="DPAPI")

from backend.automations import bundles, owned, store, team
from backend.automations.test_workflow_owners import CRON, join, sync, teams_on  # noqa: F401
from backend.store.repos import plugin_data as repo
from backend.uefn_plugins import test_scopes
from backend.uefn_plugins.test_scopes import FakeStore, _Who

account_keys = test_scopes.account_keys
fake_store = test_scopes.store
who = test_scopes.who


@pytest.fixture(autouse=True)
def plain_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(bundles, "workflow_fields", lambda: {"workflow.call": ("workflow_id",)})


def call(node_id: str, target: str) -> dict:
    return {"id": node_id, "type": "workflow.call", "x": 0, "y": 0, "config": {"workflow_id": target}}


def tree() -> dict[str, dict]:
    """Local "Kit": Main → Lib/Helper, plus an empty Kit/Lib/Later."""
    helper = store.save_workflow({"name": "Helper", "folder": "Kit/Lib", "graph": CRON})
    main = store.save_workflow({"name": "Main", "folder": "Kit", "graph": {"nodes": [call("n", helper["id"])], "edges": []}})
    store.add_folder("local", "Kit/Lib/Later")
    return {"main": main, "helper": helper}


def team_folders() -> list[str]:
    return next(o["folders"] for o in team.owners()["owners"] if o["id"] == "teamT")


def by_name(owner: str) -> dict[str, dict]:
    return {wf["name"]: store.get_workflow(wf["id"]) for wf in store.list_workflows() if wf["owner"]["id"] == owner}


def test_copy_folder_to_team_keeps_tree_and_calls_and_reaches_members(who: _Who, fake_store: FakeStore) -> None:
    ana = join(fake_store, who, "ana@x.org")
    made = tree()
    out = bundles.copy_folder("local", "Kit", "teamT", "Shared")
    assert out["folder"] == "Shared/Kit" and out["moved"] == 0
    copies = by_name("teamT")
    assert copies["Main"]["folder"] == "Shared/Kit" and copies["Helper"]["folder"] == "Shared/Kit/Lib"
    assert copies["Main"]["graph"]["nodes"][0]["config"]["workflow_id"] == copies["Helper"]["id"]
    assert copies["Helper"]["run_here"] is True
    assert {"Shared/Kit", "Shared/Kit/Lib", "Shared/Kit/Lib/Later"} <= set(team_folders())
    assert store.get_workflow(made["main"]["id"])["owner"]["id"] == "local"  # a copy leaves the source
    sync(fake_store, ana)

    join(fake_store, who, "bo@x.org")
    seen = by_name("teamT")
    assert seen["Main"]["graph"]["nodes"][0]["config"]["workflow_id"] == seen["Helper"]["id"]
    assert team_folders() == ["Shared", "Shared/Kit", "Shared/Kit/Lib", "Shared/Kit/Lib/Later"]


def test_move_folder_to_team_and_back_keeps_ids_tree_and_calls(who: _Who, fake_store: FakeStore) -> None:
    join(fake_store, who, "ana@x.org")
    made = tree()
    out = bundles.copy_folder("local", "Kit", "teamT", "", move=True)
    assert out["moved"] == 2 and out["folder"] == "Kit"
    main = store.get_workflow(made["main"]["id"])
    assert main["owner"]["id"] == "teamT" and main["folder"] == "Kit"
    assert main["graph"]["nodes"][0]["config"]["workflow_id"] == made["helper"]["id"]
    assert store.get_workflow(made["helper"]["id"])["folder"] == "Kit/Lib"
    assert [w for w in store.list_workflows() if w["owner"]["id"] == "local"] == []
    assert "Kit" not in store.folders_of("local")
    assert "Kit/Lib/Later" in team_folders()

    back = bundles.copy_folder("teamT", "Kit", "local", "Home", move=True)
    assert back["folder"] == "Home/Kit"
    assert store.get_workflow(made["helper"]["id"])["owner"]["id"] == "local"
    assert store.get_workflow(made["helper"]["id"])["folder"] == "Home/Kit/Lib"
    assert "Home/Kit/Lib/Later" in store.folders_of("local") and "Kit" not in team_folders()


def test_a_failed_move_leaves_the_source_intact(who: _Who, fake_store: FakeStore, monkeypatch) -> None:
    join(fake_store, who, "ana@x.org")
    made = tree()
    real = owned.write
    writes: list[str] = []

    def flaky(scope, doc, **kw):
        if scope["kind"] == "team" and writes:
            raise ValueError("This workflow is over 1 MB. Split it into smaller workflows.")
        if scope["kind"] == "team":
            writes.append(str(doc["id"]))
        return real(scope, doc, **kw)

    monkeypatch.setattr(owned, "write", flaky)
    with pytest.raises(ValueError, match="1 MB"):
        bundles.copy_folder("local", "Kit", "teamT", "", move=True)
    monkeypatch.setattr(owned, "write", real)
    for wf in made.values():
        kept = store.get_workflow(wf["id"])
        assert kept["owner"]["id"] == "local" and kept["folder"] == wf["folder"]
    assert [w for w in store.list_workflows() if w["owner"]["id"] == "teamT"] == []
    assert "Kit/Lib/Later" in store.folders_of("local")
    assert team_folders() == []


def test_custom_code_and_read_only_teams_are_refused_before_anything_moves(who: _Who, fake_store: FakeStore, monkeypatch) -> None:
    from backend.store.repos import workflows as runtime

    aid = join(fake_store, who, "ana@x.org")
    code = store.save_workflow({"name": "Coded", "folder": "Code", "graph": {"nodes": [
        {"id": "c", "type": "code.js", "x": 0, "y": 0, "config": {"code": "export default () => ({});"}}], "edges": []}})
    with pytest.raises(ValueError):
        bundles.copy_folder("local", "Code", "teamT", move=True)
    assert store.get_workflow(code["id"])["owner"]["id"] == "local"
    tree()
    runtime.perms_put(aid, "teamT", manage_automations=False)
    with pytest.raises(PermissionError):
        bundles.copy_folder("local", "Kit", "teamT")
    with pytest.raises(PermissionError):
        store.add_folder("teamT", "Nope")
    assert [w for w in store.list_workflows() if w["owner"]["id"] == "teamT"] == []
    # Reading a team you can't change still works: copy its folder to Local.
    runtime.perms_put(aid, "teamT", manage_automations=True)
    bundles.copy_folder("local", "Kit", "teamT")
    runtime.perms_put(aid, "teamT", manage_automations=False)
    assert bundles.copy_folder("teamT", "Kit", "local", "From team")["folder"] == "From team/Kit"


def test_empty_folders_and_renames_survive_a_sync_round(who: _Who, fake_store: FakeStore) -> None:
    ana = join(fake_store, who, "ana@x.org")
    store.add_folder("teamT", "Plans/Later")
    wf = store.save_workflow({"name": "Nightly", "folder": "Plans", "graph": CRON}, owner="teamT")
    assert repo.count_dirty(ana, "teamT") >= 2  # the folder list goes up with the workflow
    sync(fake_store, ana)
    assert team_folders() == ["Plans", "Plans/Later"]

    bo = join(fake_store, who, "bo@x.org")
    assert team_folders() == ["Plans", "Plans/Later"]
    store.move_folder("teamT", "Plans", "Ideas")
    store.add_folder("teamT", "Ideas/Empty")
    sync(fake_store, bo)

    who.be("ana@x.org")
    sync(fake_store, ana)
    assert team_folders() == ["Ideas", "Ideas/Empty", "Ideas/Later"]
    assert store.get_workflow(wf["id"])["folder"] == "Ideas"
    assert [w["name"] for w in store.list_workflows()] == ["Nightly"]  # the folder list is never a workflow
    sync(fake_store, ana)  # a quiet round changes nothing
    assert team_folders() == ["Ideas", "Ideas/Empty", "Ideas/Later"]


def test_signed_out_empty_folders_come_along_into_the_account(who: _Who) -> None:
    who.be("")
    store.add_folder("local", "Offline/Empty")
    store.save_workflow({"name": "Offline flow", "folder": "Offline", "graph": CRON})
    ana = who.be("ana@x.org")
    assert owned.local_import_count(ana) == 1
    assert team.import_local()["moved"] == 1
    assert store.folders_of("local") == ["Offline", "Offline/Empty"]
