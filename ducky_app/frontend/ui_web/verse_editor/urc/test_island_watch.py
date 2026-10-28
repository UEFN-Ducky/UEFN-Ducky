"""island_watch: one cheap lore call a round, one alert per new remote head, never a pull."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from frontend.ui_web.verse_editor.urc import island_watch as iw


class FakeLore:
    def __init__(self) -> None:
        self.remote = {"ok": True, "revision": 15, "signature": "s15", "error": ""}
        self.local = {"ok": True, "revision": 13, "signature": "s13", "error": ""}
        self.state: dict = {}
        self.calls: list[str] = []
        self.lock_rows: list[dict] = []
        self.locks_ok = True
        self.lock_calls = 0
        self.both: list[str] = []
        self.overlap_calls = 0
        self.overlap_states: list[dict] = []

    def remote_head(self, root):
        self.calls.append("remote_head")
        return dict(self.remote)

    def local_head(self, root):
        self.calls.append("local_head")
        return dict(self.local)

    def repo_state(self, root):
        self.calls.append("repo_state")
        return dict(self.state)

    def overlap(self, root, *, state=None):
        assert state is not None  # reuses the round's repo_state: no extra status/history calls
        self.overlap_calls += 1
        self.overlap_states.append(state)
        return {"ok": True, "files": [{"rel": rel, "local": "modified", "remote": "M"} for rel in self.both],
                "error": ""}

    def locks(self, root):
        self.lock_calls += 1
        if not self.locks_ok:
            return {"ok": False, "locks": [], "me": "", "error": "lore lock timed out after 60s"}
        rows = [{**lk, "path": f"{Path(root).as_posix()}/{lk['rel']}", "owner_raw": lk["owner"]}
                for lk in self.lock_rows]
        return {"ok": True, "locks": rows, "me": "Nacht", "error": ""}


def behind_15(own: bool = False) -> dict:
    return {
        "ok": True, "behind": True, "local_revision": 13, "remote_revision": 15,
        "local_signature": "s13", "remote_signature": "s15",
        "incoming": [{"revision": 15, "signature": "s15", "creator": "Mich ou", "message": "moved the sphere",
                      "own": own}],
    }


IN_SYNC_15 = {"ok": True, "behind": False, "local_revision": 15, "remote_revision": 15,
              "local_signature": "s15", "remote_signature": "s15", "incoming": []}


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    island = tmp_path / "test_ducky_v2"
    (island / ".lore").mkdir(parents=True)
    lore = FakeLore()
    out = SimpleNamespace(island=str(island), lore=lore, alerts=[], pushes=[], doc={}, locks_doc={}, away=False)
    out.open = str(island)
    monkeypatch.setattr(iw, "_lore", lambda: lore)
    monkeypatch.setattr(iw, "_open_island", lambda: out.open)
    monkeypatch.setattr(iw, "_person_away", lambda: out.away)
    monkeypatch.setattr(iw, "_alert", lambda *a: out.alerts.append(a))
    monkeypatch.setattr(iw, "_push", out.pushes.append)
    monkeypatch.setattr(iw, "_read_doc", lambda: dict(out.doc) if out.doc else None)

    def write(doc):
        out.doc = dict(doc) if doc else {}

    monkeypatch.setattr(iw, "_write_doc", write)
    monkeypatch.setattr(iw, "_read_locks", lambda: dict(out.locks_doc) if out.locks_doc else None)

    def write_locks(doc):
        out.locks_doc = dict(doc) if doc else {}

    monkeypatch.setattr(iw, "_write_locks", write_locks)
    iw.reset_for_tests()
    yield out
    iw.reset_for_tests()


def test_new_changes_from_the_other_pc_alert_once(env) -> None:
    env.lore.state = behind_15()
    assert iw.tick() == iw.ACTIVE_S
    assert env.lore.calls == ["remote_head", "repo_state"]
    assert len(env.alerts) == 1
    title, status, detail = env.alerts[0]
    assert title == "test_ducky_v2" and status == "1 new change to pull."
    assert detail.startswith("1 new change on test_ducky_v2 from Mich ou: “moved the sphere”")
    assert "revision_pull" in detail
    assert env.pushes == [{"type": "island_updates", "island": env.island, "name": "test_ducky_v2", "behind": True,
                           "count": 1, "detail": "1 new change on test_ducky_v2 from Mich ou: “moved the sphere”"}]
    assert env.doc["behind"] and env.doc["new_changes"] == 1 and env.doc["authors"] == ["Mich ou"]

    # Same remote head, same local head: two cheap calls, no new alert, never a pull.
    env.lore.calls.clear()
    iw.tick()
    assert env.lore.calls == ["remote_head", "local_head"]
    assert len(env.alerts) == 1


def test_a_pull_clears_it_and_tells_the_panel(env) -> None:
    env.lore.state = behind_15()
    iw.tick()
    env.lore.local = {"ok": True, "revision": 15, "signature": "s15", "error": ""}
    env.lore.state = IN_SYNC_15
    iw.tick()
    assert env.pushes[-1]["behind"] is False and env.pushes[-1]["detail"] == "test_ducky_v2 is up to date."
    assert env.doc["behind"] is False and env.doc["new_changes"] == 0
    env.lore.calls.clear()
    iw.tick()  # in sync: one call a round
    assert env.lore.calls == ["remote_head"]


def test_own_check_in_is_not_news(env) -> None:
    env.lore.state = behind_15(own=True)
    iw.tick()
    assert env.alerts == [] and env.pushes == []
    assert env.doc["behind"] is True and env.doc["new_changes"] == 0


def test_a_newer_push_alerts_again(env) -> None:
    env.lore.state = behind_15()
    iw.tick()
    env.lore.remote = {"ok": True, "revision": 17, "signature": "s17", "error": ""}
    state = behind_15()
    state["remote_signature"] = "s17"
    state["incoming"].insert(0, {"revision": 17, "signature": "s17", "creator": "Mich ou", "message": "more",
                                 "own": False})
    env.lore.state = state
    iw.tick()
    assert len(env.alerts) == 2 and env.alerts[1][1] == "2 new changes to pull."


def test_restart_does_not_retell_the_same_news(env) -> None:
    env.doc = {"island": env.island, "told": "s15"}
    iw.reset_for_tests()
    env.lore.state = behind_15()
    iw.tick()
    assert env.alerts == [] and env.doc["behind"] is True


def test_no_island_no_lore_call(env) -> None:
    env.open = ""
    assert iw.tick() == iw.NO_ISLAND_S
    assert env.lore.calls == []


def test_not_a_lore_island(env, tmp_path: Path) -> None:
    plain = tmp_path / "plain"
    plain.mkdir()
    env.open = str(plain)
    assert iw.tick() == iw.NOT_LORE_S
    assert env.lore.calls == []


def test_errors_back_off_then_recover(env) -> None:
    env.lore.remote = {"ok": False, "error": "offline"}
    assert iw.tick() == iw.ACTIVE_S * 2
    assert iw.tick() == iw.ACTIVE_S * 4
    for _ in range(10):
        delay = iw.tick()
    assert delay == iw.ERROR_MAX_S
    env.lore.remote = {"ok": True, "revision": 15, "signature": "s15", "error": ""}
    env.lore.state = IN_SYNC_15
    assert iw.tick() == iw.ACTIVE_S


def test_slower_when_the_person_is_away(env) -> None:
    env.away = True
    env.lore.state = IN_SYNC_15
    assert iw.tick() == iw.IDLE_S


def test_island_closed_forgets_it(env) -> None:
    env.lore.state = behind_15()
    iw.tick()
    env.open = ""
    iw.tick()
    assert env.doc == {}


def test_for_agents(env, monkeypatch: pytest.MonkeyPatch) -> None:
    assert iw.for_agents() is None
    env.lore.state = behind_15()
    iw.tick()
    out = iw.for_agents()
    assert out["behind"] is True and out["new_changes"] == 1 and out["authors"] == ["Mich ou"]
    assert out["latest"] == {"revision": 15, "by": "Mich ou", "message": "moved the sphere"}
    assert "revision_pull" in out["hint"]
    # The app that wrote it is gone: nothing watches the island any more.
    env.doc["pid"] = 999999999
    assert iw.for_agents() is None


def test_news_text() -> None:
    news = [{"creator": "A", "message": "m2"}, {"creator": "B", "message": "m1"}, {"creator": "A", "message": ""}]
    assert iw.news_text("isle", news) == "3 new changes on isle from A, B: “m2”"
    assert iw.news_text("isle", [{"creator": "", "message": ""}]) == "1 new change on isle"


LOCKED = "Content/__ExternalActors__/test_ducky_v2/1/8X/2FWY3VPOAEBYKZPCJNGYE6.uasset"
OTHER = {"rel": LOCKED, "owner": "Mich ou", "mine": False}
MINE = {"rel": "Content/Mine.uasset", "owner": "Nacht", "mine": True}


def test_locks_every_round_without_alerts(env) -> None:
    env.lore.state = IN_SYNC_15
    env.lore.lock_rows = [OTHER, MINE]
    iw.tick()
    iw.tick()  # nothing moved: still one lock query a round
    assert env.lore.lock_calls == 2 and env.alerts == [] and env.pushes == []
    doc = env.locks_doc
    assert doc["island"] == env.island and doc["me"] == "Nacht" and doc["others"] == [LOCKED]
    assert doc["every_s"] == iw.ACTIVE_S and "owner_raw" not in doc["locks"][0]
    want = {iw.norm_path(f"{env.island}/{LOCKED}"): "Mich ou"}
    assert iw.others_locks(env.island) == want
    # Any spelling of the island folder works
    assert iw.others_locks(env.island.replace("\\", "/").upper() if iw.os.name == "nt" else env.island) == want


def test_others_locks_empty_when_stale_or_other_island(env, tmp_path: Path) -> None:
    env.lore.state = IN_SYNC_15
    env.lore.lock_rows = [OTHER]
    iw.tick()
    assert iw.others_locks(str(tmp_path / "another_island")) == {}
    assert iw.others_locks("") == {}
    env.locks_doc["checked_at"] -= int(iw.LOCKS_STALE_ROUNDS * iw.ACTIVE_S + iw.LOCKS_SLACK_S + 5)
    assert iw.others_locks(env.island) == {}
    assert iw.locks_for_agents() is None
    # Away from the PC, rounds are a minute apart: the same age is still fresh
    env.locks_doc["every_s"] = iw.IDLE_S
    assert iw.others_locks(env.island) != {}


def test_lock_failure_keeps_the_last_list_until_stale(env) -> None:
    env.lore.state = IN_SYNC_15
    env.lore.lock_rows = [OTHER]
    iw.tick()
    env.lore.locks_ok = False
    iw.tick()
    assert env.locks_doc["others"] == [LOCKED]


def test_no_lock_query_when_lore_fails(env) -> None:
    env.lore.remote = {"ok": False, "error": "offline"}
    iw.tick()
    assert env.lore.lock_calls == 0 and env.locks_doc == {}


def test_island_closed_forgets_its_locks(env) -> None:
    env.lore.state = IN_SYNC_15
    env.lore.lock_rows = [OTHER]
    iw.tick()
    env.open = ""
    iw.tick()
    assert env.locks_doc == {} and iw.others_locks(env.island) == {}


def test_locks_for_agents(env) -> None:
    env.lore.state = IN_SYNC_15
    env.lore.lock_rows = [MINE]
    iw.tick()
    assert iw.locks_for_agents() is None  # only this PC's own locks: nothing to say
    env.lore.lock_rows = [OTHER, MINE]
    iw.tick()
    out = iw.locks_for_agents()
    assert out["count"] == 1 and out["locked_by_others"] == [{"file": LOCKED, "by": "Mich ou"}]
    assert out["island"] == env.island and "don't modify" in out["hint"]


def test_norm_path(tmp_path: Path) -> None:
    messy = str(tmp_path / "Isle" / "Content" / ".." / "Content" / "A.uasset")
    p = iw.norm_path(messy)
    assert "\\" not in p and ".." not in p and p == iw.norm_path(p)
    assert p.endswith("/content/a.uasset" if iw.os.name == "nt" else "/Content/A.uasset")


def test_news_names_files_changed_on_both_pcs(env) -> None:
    env.lore.both = ["Content/conflict_test.verse"]
    env.lore.state = behind_15()
    iw.tick()
    detail = env.alerts[0][2]
    assert detail.startswith("1 new change on test_ducky_v2 from Mich ou: “moved the sphere”; "
                             "1 file changed on both PCs: Content/conflict_test.verse — pull with care. ")
    assert env.pushes[0]["detail"] == detail.split(". Pull when")[0]
    assert env.doc["both_changed"] == ["Content/conflict_test.verse"]
    out = iw.for_agents()
    assert out["both_changed"] == ["Content/conflict_test.verse"] and "both_changed" in out["hint"]
    # While behind, a quiet round checks the overlap again (one diff), and keeps the list: no new alert.
    iw.tick()
    assert env.lore.overlap_calls == 2 and env.doc["both_changed"] == ["Content/conflict_test.verse"]
    assert len(env.alerts) == 1
    # Pulled: nothing changed on both sides any more.
    env.lore.local = {"ok": True, "revision": 15, "signature": "s15", "error": ""}
    env.lore.state = IN_SYNC_15
    iw.tick()
    assert env.doc["both_changed"] == [] and "both_changed" not in (iw.for_agents() or {}).get("hint", "")
    iw.tick()  # up to date: no overlap any more
    assert env.lore.overlap_calls == 2


def test_no_overlap_for_own_check_ins_or_without_news(env) -> None:
    env.lore.state = behind_15(own=True)
    iw.tick()
    iw.tick()  # behind, but nothing was told: no overlap on quiet rounds either
    assert env.lore.overlap_calls == 0 and env.doc["both_changed"] == []


def test_both_text() -> None:
    assert iw.both_text([]) == ""
    assert iw.both_text(["a", "b", "c", "d", "e"]) == "5 files changed on both PCs: a, b, c (+2 more) — pull with care"


def test_a_file_changed_here_after_the_alert_gets_one_follow_up(env) -> None:
    # Oct 27, live: the Verse file edited in VS Code showed in lore status only after the alert.
    env.lore.state = behind_15()
    env.lore.state["changes"] = []
    iw.tick()
    assert len(env.alerts) == 1 and env.doc["both_changed"] == []
    env.lore.both = ["Content/conflict_test.verse"]
    env.lore.local = {**env.lore.local, "changes": [("M", "Content/conflict_test.verse")]}
    iw.tick()
    # The overlap read this round's working tree, on the told state.
    assert env.lore.overlap_states[-1]["changes"] == [("M", "Content/conflict_test.verse")]
    assert env.lore.overlap_states[-1]["remote_signature"] == "s15"
    assert env.alerts[1] == ("test_ducky_v2", "Changed on both PCs.",
                             "Content/conflict_test.verse was also changed on this PC — pull with care. "
                             "Ask a Ducky to run revision_pull when you're ready.")
    assert env.pushes[-1]["detail"] == "Content/conflict_test.verse was also changed on this PC — pull with care"
    assert env.doc["both_changed"] == ["Content/conflict_test.verse"] and env.doc["behind"] is True
    assert "both_changed" in iw.for_agents()["hint"]
    # Same files next round: no second alert. One left again: the list follows, still no alert.
    iw.tick()
    assert len(env.alerts) == 2
    env.lore.both = []
    iw.tick()
    assert len(env.alerts) == 2 and env.doc["both_changed"] == []
