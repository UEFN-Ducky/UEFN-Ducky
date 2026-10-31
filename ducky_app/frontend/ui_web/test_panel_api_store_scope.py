"""The scope bar's bridge: switching away from a shared space sends its queued changes
first; Retry and Drop my change for changes that aren't going out; Upload my copy and
Clear my copy, for the space's whole copy, after its data was reset on the server.

The drawer of a team copy (the new sync, a fake team server): people, locks, changes,
conflicts, mode and head; lock, release, ask and break; compare and resolve; history,
restore and undo; Auto/Manual with Push and Pull; a panel write refused with who holds
the item; unsent changes of a lost space saved to Local; a copy on the older sync says
those aren't there yet.
"""

from __future__ import annotations

import sys
import threading
import time
from typing import Any

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="DPAPI")

from backend.store.repos import plugin_data as repo
from backend.uefn_plugins import scopes, spaces
from backend.uefn_plugins import test_scopes
from backend.uefn_plugins.scopes import PluginData
from backend.uefn_plugins.test_scopes import FakeSpaces, open_space
import frontend.ui_web.panel_api  # noqa: F401, E402 - panel_api_store is imported through it
from frontend.ui_web import panel_api_store
from frontend.ui_web.panel_api_store import PanelApiStoreMixin

account_keys = test_scopes.account_keys
who = test_scopes.who

PLUGIN = "brainrot-tcg"


class Sending(FakeSpaces):
    """A round sends every queued change (rev 1) and notes where the plugin was linked."""

    def __init__(self) -> None:
        super().__init__()
        self.seen: list[tuple[str, bool, str]] = []
        self.hang: threading.Event | None = None

    def sync(self, account: str, space_id: str, *, force: bool = False) -> dict[str, Any]:
        self.seen.append((space_id, force, repo.link_get(account, PLUGIN)))
        if self.hang is not None:
            self.hang.wait(30)
            return {"state": "ok", "changed": [], "error": ""}
        for row in repo.dirty(account, space_id, 100):
            repo.mark_pushed(account, space_id, row["plugin_id"], row["kind"], row["key"], rev=1, sha256=row["sha256"],
                             pushed_delete=bool(row["deleted"]))
        return {"state": "ok", "changed": [], "error": ""}


class Api(PanelApiStoreMixin):
    def __init__(self) -> None:
        self.pushed: list[dict[str, Any]] = []

    def _push_panel(self, event: dict[str, Any]) -> None:
        self.pushed.append(event)


@pytest.fixture
def fake(account_keys, monkeypatch: pytest.MonkeyPatch) -> Sending:
    from backend.uefn_plugins import host

    monkeypatch.setattr(host, "reload_single_plugin", lambda pid: None)
    impl = Sending()
    spaces.install_for_tests(impl)
    yield impl
    spaces.reset_for_tests()


def test_switching_away_sends_the_old_spaces_changes_before_relinking(who, fake: Sending) -> None:
    aid = open_space(fake, who, "ana@x.org", "spaceT", "Alpha")
    PluginData(PLUGIN).put("card.pip", {"name": "Pip"})
    out = Api().plugin_scope_set(PLUGIN, "personal")
    assert fake.seen[0] == ("spaceT", True, "spaceT")  # still linked to the team while it sent
    assert out["unsent"] == 0 and out["unsentLabel"] == "Alpha" and out["scope"]["kind"] == "personal"
    assert repo.link_get(aid, PLUGIN) == "personal" and repo.count_dirty(aid, "spaceT") == 0


def test_a_switch_waits_for_the_send_only_so_long_and_says_what_stayed(who, fake: Sending, monkeypatch) -> None:
    monkeypatch.setattr(panel_api_store, "SWITCH_SEND_TIMEOUT_S", 0.3)
    aid = open_space(fake, who, "ana@x.org", "spaceT", "Alpha")
    PluginData(PLUGIN).put("card.pip", {"name": "Pip"})
    fake.hang = threading.Event()
    try:
        began = time.monotonic()
        out = Api().plugin_scope_set(PLUGIN, "personal")
        assert time.monotonic() - began < 2.0
    finally:
        fake.hang.set()
    assert out["unsent"] == 1 and repo.link_get(aid, PLUGIN) == "personal"
    # Nothing queued: no round before the switch, and no count.
    fake.seen.clear()
    fake.hang = None
    out = Api().plugin_scope_set(PLUGIN, "spaceT")
    assert "unsent" not in out


def test_one_pick_switches_every_plugin_and_the_overview_lists_them(who, fake: Sending, monkeypatch) -> None:
    from backend.uefn_plugins import store

    monkeypatch.setattr(store, "get_enabled_plugin_ids", lambda: [PLUGIN, "forge"])
    aid = open_space(fake, who, "ana@x.org", "spaceT", "Alpha")
    scopes.link_plugin("forge", "spaceT")
    PluginData("forge").put("scene.one", {"name": "One"})
    over = Api().plugin_team_overview()
    assert (over["current"], over["label"]) == ("spaceT", "Alpha")
    assert [r["pluginId"] for r in over["plugins"]] == [PLUGIN, "forge"]
    assert {"conflictList", "locks", "people", "head", "mode"} <= set(over["plugins"][0])
    out = Api().plugin_scope_set_all("personal")
    assert out["ok"] and sorted(out["switched"]) == [PLUGIN, "forge"]
    assert repo.link_get(aid, PLUGIN) == "personal" and repo.link_get(aid, "forge") == "personal"
    assert Api().plugin_team_overview() == {"ok": True, "current": "personal", "label": "Local", "plugins": []}
    assert Api().plugin_scope_set_all("")["ok"] is False


def test_drop_clears_a_blocked_change_and_the_team_copy_comes_back(who, fake: Sending) -> None:
    aid = open_space(fake, who, "ana@x.org", "spaceT", "Alpha")
    data = PluginData(PLUGIN)
    data.put("card.new", {"name": "Never sent"})
    data.put("card.old", {"name": "Edited"})
    repo.set_rev(aid, "spaceT", PLUGIN, "doc", "card.old", 7)
    repo.set_state(aid, "spaceT", PLUGIN, "doc", "card.old", "refused", "Too big for the team's plan.")
    repo.sync_put(aid, "spaceT", cursor_rev=20)
    for _ in range(repo.BLOCK_AFTER):
        repo.note_attempt(aid, "spaceT", PLUGIN, "doc", "card.new", "Can't read it.")
    assert spaces.scope_status(PLUGIN)["blocked"] == 1
    api = Api()
    assert api.plugin_scope_drop(PLUGIN, "doc", "card.new") == {"ok": True, "dropped": 1}
    assert repo.get(aid, "spaceT", PLUGIN, "doc", "card.new") is None
    status = spaces.scope_status(PLUGIN)
    assert status["blocked"] == 0 and [item["key"] for item in status["items"]] == ["card.old"]
    assert repo.sync_get(aid, "spaceT")["cursor_rev"] == 20  # it was never on the server
    # An item the team has: dropped here, and the next pull starts back at its rev.
    assert api.plugin_scope_drop(PLUGIN, "doc", "card.old")["ok"] is True
    assert repo.sync_get(aid, "spaceT")["cursor_rev"] == 7
    assert api.plugin_scope_drop(PLUGIN, "doc", "card.old") == {"ok": False, "error": "That change was already sent or dropped."}
    assert {"type": "plugin_scope_changed", "plugins": [PLUGIN]} in api.pushed


def test_drop_removes_a_files_change_with_its_file(who, fake: Sending) -> None:
    aid = open_space(fake, who, "ana@x.org", "spaceT", "Alpha")
    data = PluginData(PLUGIN)
    data.put_file("art/pip.png", b"PNG")
    repo.set_state(aid, "spaceT", PLUGIN, "asset", "art/pip.png", "refused", "Team storage is full.")
    path = scopes.asset_file(scopes.active_scope(PLUGIN), PLUGIN, "art/pip.png")
    assert path.is_file()
    assert Api().plugin_scope_drop(PLUGIN, "asset", "art/pip.png")["ok"] is True
    assert not path.is_file() and repo.get(aid, "spaceT", PLUGIN, "asset", "art/pip.png") is None


def test_drop_takes_only_the_change_held_back(who, fake: Sending) -> None:
    aid = open_space(fake, who, "ana@x.org", "spaceT", "Alpha")
    data = PluginData(PLUGIN)
    data.put("card.a", {"name": "Queued"})
    api = Api()
    gone = {"ok": False, "error": "That change was already sent or dropped."}
    assert api.plugin_scope_drop(PLUGIN, "doc", "card.a") == gone  # queued, maybe being sent: stays
    repo.set_state(aid, "spaceT", PLUGIN, "doc", "card.a", "blocked", "Can't read it.")
    held = repo.get(aid, "spaceT", PLUGIN, "doc", "card.a")
    data.put("card.a", {"name": "Edited since"})  # a newer edit lands after the bar read the row
    assert repo.drop_held(aid, "spaceT", PLUGIN, "doc", "card.a", expect=held) is False
    assert data.get("card.a") == {"name": "Edited since"}


def test_a_drops_rewind_holds_against_a_round_already_running(who, fake: Sending) -> None:
    aid = open_space(fake, who, "ana@x.org", "spaceT", "Alpha")
    PluginData(PLUGIN).put("card.old", {"name": "Edited"})
    repo.set_rev(aid, "spaceT", PLUGIN, "doc", "card.old", 7)
    repo.set_state(aid, "spaceT", PLUGIN, "doc", "card.old", "refused", "Too big.")
    repo.sync_put(aid, "spaceT", cursor_rev=20, called_at=time.time() - 60)  # a round started a minute ago
    assert Api().plugin_scope_drop(PLUGIN, "doc", "card.old")["ok"] is True
    assert repo.sync_get(aid, "spaceT")["cursor_rev"] == 7
    # That round ends and writes the cursor it reached: the rewind holds.
    repo.sync_put(aid, "spaceT", cursor_rev=30)
    assert repo.sync_get(aid, "spaceT")["cursor_rev"] == 7
    # A round that started after the drop pulled from 7: its cursor stands, and the hold ends.
    repo.sync_put(aid, "spaceT", called_at=time.time() + repo.REWIND_GRACE_S + 1)
    repo.sync_put(aid, "spaceT", cursor_rev=30)
    repo.sync_put(aid, "spaceT", cursor_rev=31)
    assert repo.sync_get(aid, "spaceT")["cursor_rev"] == 31


def test_retry_puts_refused_and_blocked_changes_back_in_the_queue(who, fake: Sending) -> None:
    aid = open_space(fake, who, "ana@x.org", "spaceT", "Alpha")
    data = PluginData(PLUGIN)
    data.put("card.a", {"name": "A"})
    data.put("card.b", {"name": "B"})
    repo.set_state(aid, "spaceT", PLUGIN, "doc", "card.a", "refused", "Too big for the team's plan.")
    repo.set_state(aid, "spaceT", PLUGIN, "doc", "card.b", "blocked", "Can't read it.")
    assert repo.count_dirty(aid, "spaceT", PLUGIN) == 0
    api = Api()
    assert api.plugin_scope_retry(PLUGIN, "card.a") == {"ok": True, "retried": 1}
    assert repo.get(aid, "spaceT", PLUGIN, "doc", "card.a")["sync_state"] == ""
    assert api.plugin_scope_retry(PLUGIN) == {"ok": True, "retried": 1}
    assert repo.count_dirty(aid, "spaceT", PLUGIN) == 2
    assert api.plugin_scope_retry(PLUGIN, "card.a")["ok"] is False  # nothing waiting any more
    api.plugin_scope_set(PLUGIN, "personal")
    assert api.plugin_scope_retry(PLUGIN) == {"ok": False, "error": "This plugin's data isn't shared."}


def _reset_copy(fake: Sending, who) -> str:
    """Alpha holds this plugin's and forge's data, all sent once; then its data is reset on
    the server."""
    aid = open_space(fake, who, "ana@x.org", "spaceT", "Alpha")
    scopes.link_plugin("forge", "spaceT")
    data = PluginData(PLUGIN)
    data.put("card.a", {"name": "A"})
    data.put("card.b", {"name": "B"})
    data.put_file("art/a.png", b"PNG")
    PluginData("forge").put("scene.one", {"name": "One"})
    data.delete("card.b")
    for row in repo.dirty(aid, "spaceT", 100):
        repo.mark_pushed(aid, "spaceT", row["plugin_id"], row["kind"], row["key"], rev=5, sha256=row["sha256"],
                         pushed_delete=bool(row["deleted"]))
    data.put("card.c", {"name": "C"})
    repo.set_state(aid, "spaceT", PLUGIN, "doc", "card.c", "refused", "Team storage is full.")
    repo.sync_put(aid, "spaceT", state="reset", error="This team's data was reset on the server.", cursor_rev=40)
    return aid


def test_after_a_reset_retry_and_drop_wait_for_upload_or_clear(who, fake: Sending) -> None:
    aid = _reset_copy(fake, who)
    api = Api()
    first = {"ok": False, "error": panel_api_store.RESET_FIRST}
    assert api.plugin_scope_retry(PLUGIN) == first  # Retry all never uploads the whole copy
    assert api.plugin_scope_retry(PLUGIN, "card.c") == first
    assert api.plugin_scope_drop(PLUGIN, "doc", "card.c") == first
    assert api.plugin_scope_drop(PLUGIN, "", "") == first
    assert repo.get(aid, "spaceT", PLUGIN, "doc", "card.c")["sync_state"] == "refused"
    assert repo.get(aid, "spaceT", "forge", "doc", "scene.one")["dirty"] == 0
    assert repo.sync_get(aid, "spaceT")["state"] == "reset"
    status = spaces.scope_status(PLUGIN)
    assert status["state"] == "reset" and status["resetContents"] == [PLUGIN, "forge"]


def test_upload_my_copy_sends_the_whole_spaces_copy_again_as_new(who, fake: Sending) -> None:
    aid = _reset_copy(fake, who)
    data = PluginData(PLUGIN)
    with pytest.raises(scopes.ReadOnlyScope):
        data.put("card.d", {"name": "D"})
    api = Api()
    assert api.space_reset_settle("spaceT", "upload") == {"ok": True, "queued": 4}
    for plugin, kind, key in ((PLUGIN, "doc", "card.a"), (PLUGIN, "asset", "art/a.png"), (PLUGIN, "doc", "card.c"),
                              ("forge", "doc", "scene.one")):
        row = repo.get(aid, "spaceT", plugin, kind, key)
        assert (row["rev"], row["dirty"], row["sync_state"]) == (0, 1, "")
    assert repo.get(aid, "spaceT", PLUGIN, "doc", "card.b") is None  # nothing is left there to delete
    st = repo.sync_get(aid, "spaceT")
    assert (st["state"], st["error"], st["cursor_rev"]) == ("ok", "", 0)
    data.put("card.d", {"name": "D"})  # writable again
    assert api.space_reset_settle("spaceT", "upload") == {"ok": False, "error": "Alpha's data wasn't reset on the server."}
    assert {"type": "plugin_scope_changed", "plugins": [PLUGIN, "forge"]} in api.pushed


def test_clear_my_copy_clears_the_whole_spaces_copy_and_sends_nothing(who, fake: Sending) -> None:
    aid = _reset_copy(fake, who)
    folder = scopes.scopes_root(aid) / "spaceT"
    assert (folder / PLUGIN).is_dir()
    api = Api()
    assert api.space_reset_settle("spaceT", "clear") == {"ok": True, "cleared": 4}
    for plugin in (PLUGIN, "forge"):
        assert repo.rows(aid, "spaceT", plugin, "doc") == [] and repo.rows(aid, "spaceT", plugin, "asset") == []
    assert repo.count_dirty(aid, "spaceT") == 0 and not folder.exists()
    st = repo.sync_get(aid, "spaceT")
    assert (st["state"], st["cursor_rev"], st["label"]) == ("ok", 0, "Alpha")
    assert repo.links(aid) == {PLUGIN: "spaceT", "forge": "spaceT"}  # still linked: the team's data comes down
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and not any(e.get("switched") for e in api.pushed):
        time.sleep(0.02)
    assert {"type": "plugin_scope_changed", "plugins": [PLUGIN, "forge"], "switched": True} in api.pushed
    assert api.space_reset_settle("spaceT", "throw away")["ok"] is False


# --------------------------------------------------------------------------- team copies: the drawer's calls

team_pc = test_scopes.team_pc
TEAM = "cards"


class Team(spaces.Provider):
    """Every plugin is on the new sync, through Ana's client of the fake server."""

    def __init__(self, backend: Any) -> None:
        self.backend = backend

    def enabled(self) -> bool:
        return True

    def ledger(self, account: str, space_id: str, plugin: str) -> Any:
        return self.backend


@pytest.fixture
def team(team_pc, monkeypatch: pytest.MonkeyPatch):
    """Ana on the new sync with Ben; rounds a call starts in the background are noted, not run."""
    pc, ben, world = team_pc
    spaces.install_for_tests(Team(pc.backend))
    started: list[tuple[str, bool]] = []

    def note_round(account: str, space: str, force: bool = False, on_done: Any = None) -> dict[str, Any]:
        started.append((space, force))
        return {"ok": True, "started": True}

    monkeypatch.setattr(spaces, "sync_space", note_round)
    yield pc, ben, world, started
    spaces.reset_for_tests()


def _conflict(pc: Any, ben: Any) -> None:
    pc.data.put("card.pip", {"hp": 1})
    pc.run()
    ben.put_doc("card.pip", {"hp": 3})
    pc.data.put("card.pip", {"hp": 2})
    assert pc.run()["conflicts"] == 1


def test_the_drawer_reads_people_locks_changes_conflicts_mode_and_head(team) -> None:
    from backend.uefn_plugins import vcs_engine
    from backend.uefn_plugins.ledger import LockUnit

    pc, ben, _, _ = team
    _conflict(pc, ben)
    ben.client.lock("spaceT", TEAM, [LockUnit("doc:card.zed", "Zed")], "best", {})
    editing = [{"unitName": "Zed", "actor": "agent", "ducky": "Builder", "since": 5.0}]
    ben.client.beat("spaceT", {"label": "Ben's PC", "plugins": {TEAM: {"viewing": True, "editing": editing}}})
    assert vcs_engine.beat(pc.account, "spaceT", pc.backend, [TEAM], force=True)["ok"]
    pc.data.put("card.new", {"v": 1})
    out = Api().plugin_scope_activity(TEAM)
    assert out["ok"] and out["scope"]["kind"] == "shared" and out["state"] == "ok"
    assert (out["head"], out["mode"], out["pending"], out["nextTry"], out["importing"]) == (2, "auto", 1, None, None)
    people = {p["name"]: p for p in out["people"]}
    assert people["Ana"]["me"] and not people["Ben"]["me"] and people["Ben"]["device"] == "Ben's PC"
    assert people["Ben"]["plugins"][TEAM] == {"viewing": True, "editing": editing, "live": ""}  # not live
    assert [(lk["unit"], lk["name"], lk["holder"], lk["mine"]) for lk in out["locks"]] == [
        ("doc:card.zed", "Zed", "AI (Ben's ducky Builder)", False)]  # Ben's ducky edits Zed
    assert out["locks"][0]["leaseLeft"] > 200
    assert [(c["kind"], c["key"], c["reason"], c["by"], c["theirsSeq"]) for c in out["conflicts"]] == [
        ("doc", "card.pip", "changed", "Ben", 2)]
    assert out["changes"]["incoming"] == 0 and out["changes"]["blocked"] == []
    assert [(o["key"], o["state"]) for o in out["changes"]["outgoing"]] == [("card.new", "queued")]


def test_lock_release_ask_and_break_from_the_drawer(team) -> None:
    from backend.store.repos import space_vcs
    from backend.uefn_plugins.ledger import LockUnit

    pc, ben, world, _ = team
    api = Api()
    got = api.plugin_scope_lock(TEAM, "card.pip", "Pip")
    assert (got["ok"], got["state"], got["unit"]) == (True, "granted", "doc:card.pip")
    assert [(lk["unit"], lk["name"], lk["reason"]) for lk in space_vcs.locks(pc.account, "spaceT", TEAM)] == [
        ("doc:card.pip", "Pip", "manual")]
    theirs = ben.client.lock("spaceT", TEAM, [LockUnit("doc:card.pip")], "best", {})
    assert type(theirs["doc:card.pip"]).__name__ == "LockInfo"
    assert api.plugin_scope_unlock(TEAM, "doc:card.pip") == {"ok": True, "error": "", "unit": "doc:card.pip"}
    assert space_vcs.locks(pc.account, "spaceT", TEAM) == [] and world.server.space("spaceT").locks == {}
    ben.client.lock("spaceT", TEAM, [LockUnit("doc:card.zed", "Zed")], "best", {})
    assert api.plugin_scope_ask(TEAM, "doc:card.zed")["ok"]
    assert world.server.asks == [("spaceT", TEAM, "doc:card.zed", "u_ana")]
    refused = api.plugin_scope_break(TEAM, "doc:card.zed")
    assert refused["ok"] is False and "owners and admins" in refused["error"]
    bad = api.plugin_scope_lock(TEAM, "../x")
    assert bad == {"ok": False, "error": "invalid unit: '../x' (use doc:<key> or asset:<path>)"}


def test_resolve_compares_and_keeps_mine(team) -> None:
    pc, ben, _, started = team
    _conflict(pc, ben)
    api = Api()
    diff = api.plugin_scope_resolve(TEAM, "doc", "card.pip", "compare")
    assert diff["ok"] and '-  "hp": 3' in diff["diff"] and '+  "hp": 2' in diff["diff"]
    assert api.plugin_scope_resolve(TEAM, "doc", "card.pip", "both")["ok"] is False
    assert api.plugin_scope_resolve(TEAM, "doc", "card.pip", "mine") == {"ok": True}
    assert started == [("spaceT", True)]  # what was kept goes out on a round
    assert pc.run()["pushed"] == 1 and ben.doc("card.pip") == {"hp": 2}
    gone = api.plugin_scope_resolve(TEAM, "doc", "card.pip", "theirs")
    assert gone == {"ok": False, "error": "There is no conflict here any more."}


def test_history_restore_and_mode(team) -> None:
    pc, _, _, _ = team
    api = Api()
    pc.data.put("card.pip", {"hp": 1})
    pc.run()
    pc.data.put("card.pip", {"hp": 2})
    pc.run()
    hist = api.plugin_scope_history(TEAM)
    assert hist["ok"] and [e["seq"] for e in hist["entries"]] == [2, 1]
    assert hist["entries"][0]["items"] == [["doc", "card.pip"]]
    versions = api.plugin_scope_history(TEAM, {"kind": "doc", "key": "card.pip", "limit": 1})
    assert [e["seq"] for e in versions["entries"]] == [2] and versions["more"] is True
    out = api.plugin_scope_restore(TEAM, {"items": [{"kind": "doc", "key": "card.pip", "seq": 1}]})
    assert out["ok"] and out["seq"] == 3 and out["skipped"] == [] and pc.data.get("card.pip") == {"hp": 1}
    out = api.plugin_scope_restore(TEAM, {"undoSeq": 3})
    assert out["ok"] and pc.data.get("card.pip") == {"hp": 2}
    assert api.plugin_scope_restore(TEAM, {})["ok"] is False
    assert api.plugin_scope_mode(TEAM, "manual") == {"ok": True, "mode": "manual"}
    assert Api().plugin_scope_activity(TEAM)["mode"] == "manual"
    assert api.plugin_scope_mode(TEAM, "sometimes") == {"ok": False, "error": "Choose Auto or Manual."}


def test_push_and_pull_now(team) -> None:
    pc, ben, _, _ = team
    api = Api()
    api.plugin_scope_mode(TEAM, "manual")
    pc.data.put("card.pip", {"hp": 1})
    ben.put_doc("card.zed", {"hp": 7})

    def wait_synced() -> None:
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and not any(e.get("synced") for e in api.pushed):
            time.sleep(0.02)
        assert any(e.get("synced") for e in api.pushed)
        api.pushed.clear()

    assert api.plugin_scope_push(TEAM, "Tuned Pip") == {"ok": True, "started": True}
    wait_synced()
    assert ben.doc("card.pip") == {"hp": 1} and pc.data.get("card.zed") is None  # Push only sends
    assert ben.client.history("spaceT", TEAM).entries[0].message == "Tuned Pip"
    assert api.plugin_scope_pull(TEAM) == {"ok": True, "started": True}
    wait_synced()
    assert pc.data.get("card.zed") == {"hp": 7}


def test_a_refused_panel_write_says_who_holds_it(team) -> None:
    from backend.uefn_plugins import vcs_engine
    from backend.uefn_plugins.ledger import LockUnit

    pc, ben, _, _ = team
    ben.client.lock("spaceT", TEAM, [LockUnit("doc:card.pip", "Pip")], "best", {})
    vcs_engine.refresh(pc.account, "spaceT", [TEAM], pc.backend)
    out = Api().plugin_data(TEAM, "data.put", {"key": "card.pip", "value": {"hp": 1}})
    assert (out["ok"], out["code"]) == (False, "locked") and out["error"].startswith("Pip is locked by Ben since ")
    lock = {k: out["lock"][k] for k in ("unit", "name", "holder")}
    assert lock == {"unit": "doc:card.pip", "name": "Pip", "holder": "Ben"}


def test_unsent_changes_of_a_lost_space_are_saved_to_local(team) -> None:
    pc, _, _, _ = team
    api = Api()
    pc.data.put("card.pip", {"hp": 2})
    assert api.plugin_scope_save_unsent(TEAM) == {"ok": False, "error": "Only for a space you lost access to."}
    repo.sync_put(pc.account, "spaceT", state="lost")
    status = api.plugin_scope_activity(TEAM)
    assert (status["state"], status["unsent"], status["unsentChosen"]) == ("lost", 1, False)
    out = api.plugin_scope_save_unsent(TEAM)
    assert out["ok"] and out["saved"] == [{"plugin": TEAM, "kind": "doc", "key": "card.pip", "localKey": "card.pip"}]
    assert pc.data.personal().get("card.pip") == {"hp": 2}
    status = api.plugin_scope_activity(TEAM)
    assert (status["unsent"], status["unsentChosen"]) == (0, True)


def test_a_copy_on_the_older_sync_has_no_locks_or_history(who, fake: Sending) -> None:
    open_space(fake, who, "ana@x.org", "spaceT", "Alpha")
    api = Api()
    for out in (api.plugin_scope_lock(PLUGIN, "card.pip"), api.plugin_scope_history(PLUGIN),
                api.plugin_scope_push(PLUGIN), api.plugin_scope_mode(PLUGIN, "manual")):
        assert out == {"ok": False, "error": spaces.NO_LEDGER_ERROR}
    status = api.plugin_scope_activity(PLUGIN)
    assert (status["engine"], status["conflicts"]) == ("v1", [])
    assert not {"head", "mode", "people", "locks"} & set(status)  # the drawer shows none of the new sync's tables
