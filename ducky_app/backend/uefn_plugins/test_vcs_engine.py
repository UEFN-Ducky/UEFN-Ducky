"""The version engine against FakeLedger: this PC (Ana, a real ducky.db) and a teammate
(Ben, a client of the same fake server).

Pushes and pulls round-trip; disjoint edits merge and push as merged; a clash keeps mine
in conflicts while theirs is current; files keep both; edit against delete; a crash
between upload and commit replays the same changeset and loses no later edit; writers in
another process or on another connection racing the apply lose nothing; resync; locks
(successor, locked out, broken); refused, offline, manual mode, groups, blocked
downloads, workflows, on_change, presence, restore and undo, unsent changes of a lost
space saved to Local.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Callable

import pytest

# Every seal/open is DPAPI: these checks need Windows.
pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="DPAPI")

from backend.store.repos import plugin_data as repo
from backend.store.repos import space_vcs
from backend.uefn_plugins import data_crypto, ledger_conformance as conf, scopes, vcs_engine
from backend.uefn_plugins.ledger import Committed, Error, LockUnit, Offline
from backend.uefn_plugins.scopes import PluginData

SPACE, PLUGIN, WF = "spaceT", "cards", "ducky.automations"
APP_DIR = str(Path(__file__).resolve().parents[2])


@pytest.fixture
def aid(monkeypatch: pytest.MonkeyPatch):
    """Ana signed in, ``cards`` linked to the space, already pulled once."""
    from frontend import duckyos_account

    key = "https://uefnducky.org|ana@example.com"
    data_crypto.reset_for_tests()
    monkeypatch.setattr(duckyos_account, "account_key", lambda blob=None: key)
    monkeypatch.setattr(data_crypto, "_fetch_adk", lambda: hashlib.sha256(b"adk/" + key.encode()).digest())
    account = scopes.account_id()
    repo.link_set(account, PLUGIN, SPACE)
    repo.sync_put(account, SPACE, label="Alpha", synced_at=time.time())
    yield account
    scopes.forget_change_listeners(PLUGIN)
    data_crypto.reset_for_tests()


@pytest.fixture
def world():
    w = conf.FakeWorld()
    yield w
    w.server.rmtree()


class PC:
    """This PC: Ana's engine rounds on the real database."""

    def __init__(self, world: conf.FakeWorld, account: str) -> None:
        self.account = account
        self.backend = world.server.client("u_ana", vcs_engine.device_id(), name="Ana")
        self.data = PluginData(PLUGIN)

    def run(self, plugin: str = PLUGIN, backend: Any = None, **kw: Any) -> dict[str, Any]:
        return vcs_engine.run(self.account, SPACE, plugin, backend or self.backend, force=True, **kw)

    def row(self, key: str, kind: str = "doc", plugin: str = PLUGIN) -> dict[str, Any] | None:
        return space_vcs.row(None, self.account, SPACE, plugin, kind, key)

    def put_raw(self, plugin: str, key: str, value: Any) -> None:
        raw = scopes.encode_doc(value)
        repo.put(self.account, SPACE, plugin, "doc", key, value=data_crypto.seal_text(raw.decode(), self.account),
                 size=len(raw), sha256=hashlib.sha256(raw).hexdigest(), dirty=True)

    def get_raw(self, plugin: str, key: str) -> Any:
        r = self.row(key, plugin=plugin)
        return json.loads(data_crypto.open_text(r["value"], self.account)) if r and r["value"] else None


class Ben:
    """The teammate: commits straight to the server."""

    def __init__(self, world: conf.FakeWorld) -> None:
        self.server = world.server
        self.client = world.server.client("u_ben", "d_ben", name="Ben")

    def seq_of(self, kind: str, key: str, plugin: str = PLUGIN) -> int:
        item = self.server.space(SPACE).repo(plugin).items.get((kind, key))
        return item.seq if item else 0

    def _push(self, plugin: str, *items: Any, **kw: Any) -> int:
        out = self.client.push(SPACE, plugin, conf.changeset(*items, **kw))
        assert isinstance(out, Committed), out
        return out.seq

    def put_doc(self, key: str, value: Any, plugin: str = PLUGIN, **kw: Any) -> int:
        item = conf.put_item("doc", key, scopes.encode_doc(value), base=self.seq_of("doc", key, plugin))
        return self._push(plugin, item, **kw)

    def put_file(self, path: str, data: bytes) -> int:
        return self._push(PLUGIN, conf.put_item("asset", path, data, base=self.seq_of("asset", path)))

    def delete(self, kind: str, key: str) -> int:
        return self._push(PLUGIN, conf.del_item(kind, key, base=self.seq_of(kind, key)))

    def doc(self, key: str, plugin: str = PLUGIN) -> Any:
        item = self.server.space(SPACE).repo(plugin).items.get(("doc", key))
        return json.loads(item.content) if item and item.op == "put" else None

    def head(self, plugin: str = PLUGIN) -> int:
        return self.server.space(SPACE).repo(plugin).head


@pytest.fixture
def pc(world: conf.FakeWorld, aid: str) -> PC:
    return PC(world, aid)


@pytest.fixture
def ben(world: conf.FakeWorld) -> Ben:
    return Ben(world)


def _mine(c: dict[str, Any], account: str) -> Any:
    return json.loads(data_crypto.open_text(c["mine_value"], account))


# --------------------------------------------------------------------------- push + pull


def test_changes_go_out_and_come_in(pc: PC, ben: Ben) -> None:
    pc.data.put("card.pip", {"hp": 1, "name": "Pip"})
    pc.data.put("card.zed", {"hp": 2})
    pc.data.put_file("img/pip.png", b"\x89PNG-1")
    out = pc.run()
    assert (out["state"], out["pushed"]) == ("ok", 1)
    assert ben.head() == 1 and ben.doc("card.pip") == {"hp": 1, "name": "Pip"}
    for key, kind in (("card.pip", "doc"), ("card.zed", "doc"), ("img/pip.png", "asset")):
        r = pc.row(key, kind)
        assert (r["dirty"], r["rev"]) == (0, 1), key
    assert space_vcs.base_get(pc.account, SPACE, PLUGIN, "card.pip")["seq"] == 1
    assert space_vcs.head_get(pc.account, SPACE, PLUGIN)["seq"] == 1

    ben.put_doc("card.zed", {"hp": 3})
    ben.put_file("img/zed.png", b"Z")
    ben.delete("doc", "card.pip")
    out = pc.run()
    assert (out["state"], out["changed"], out["pulled"], out["pushed"]) == ("ok", [PLUGIN], 3, 0)
    assert pc.data.get("card.zed") == {"hp": 3}
    assert pc.data.get_file("img/zed.png") == b"Z"
    assert pc.data.get("card.pip") is None and pc.row("card.pip") is None
    assert space_vcs.head_get(pc.account, SPACE, PLUGIN)["seq"] == 4
    assert space_vcs.unit_seqs(pc.account, SPACE, PLUGIN, ["doc:card.zed"]) == {"doc:card.zed": 2}
    assert repo.sync_get(pc.account, SPACE)["synced_at"] > 0


def test_disjoint_edits_merge_and_go_out_as_merged(pc: PC, ben: Ben) -> None:
    pc.data.put("card.pip", {"hp": 1, "name": "Pip"})
    pc.run()
    ben.put_doc("card.pip", {"hp": 1, "name": "Pipo"})
    pc.data.put("card.pip", {"hp": 2, "name": "Pip"})
    out = pc.run()
    assert out["state"] == "ok" and out["conflicts"] == 0
    assert pc.data.get("card.pip") == {"hp": 2, "name": "Pipo"}
    assert ben.doc("card.pip") == {"hp": 2, "name": "Pipo"} and ben.head() == 3
    assert ben.client.history(SPACE, PLUGIN).entries[0].message == "Merged with a change by Ben"
    assert (pc.row("card.pip")["dirty"], pc.row("card.pip")["rev"]) == (0, 3)


def test_a_clash_keeps_mine_as_a_conflict_while_theirs_is_current(pc: PC, ben: Ben) -> None:
    pc.data.put("card.pip", {"hp": 1})
    pc.run()
    ben.put_doc("card.pip", {"hp": 3})
    pc.data.put("card.pip", {"hp": 2})
    out = pc.run()
    assert out["conflicts"] == 1 and ben.head() == 2  # mine didn't go over theirs
    assert pc.data.get("card.pip") == {"hp": 3}
    r = pc.row("card.pip")
    assert (r["sync_state"], r["dirty"], r["rev"]) == ("conflict", 0, 2)
    c = space_vcs.conflict_get(pc.account, SPACE, PLUGIN, "doc", "card.pip")
    assert _mine(c, pc.account) == {"hp": 2} and (c["theirs_by"], c["theirs_seq"], c["reason"]) == ("Ben", 2, "changed")
    assert vcs_engine.status(pc.account, SPACE, PLUGIN)["conflicts"][0]["key"] == "card.pip"
    assert pc.run()["pushed"] == 0  # held until the person picks

    assert vcs_engine.resolve(pc.account, SPACE, PLUGIN, "doc", "card.pip", "mine") == {"ok": True}
    assert pc.data.get("card.pip") == {"hp": 2}
    assert pc.run()["pushed"] == 1 and ben.doc("card.pip") == {"hp": 2}
    assert space_vcs.conflicts(pc.account, SPACE) == [] and pc.row("card.pip")["dirty"] == 0


def test_a_file_changed_on_both_sides_keeps_both(pc: PC, ben: Ben) -> None:
    pc.data.put_file("art/a.png", b"v1")
    pc.run()
    ben.put_file("art/a.png", b"theirs")
    pc.data.put_file("art/a.png", b"mine")
    assert pc.run()["conflicts"] == 1
    assert pc.data.get_file("art/a.png") == b"theirs"
    c = space_vcs.conflict_get(pc.account, SPACE, PLUGIN, "asset", "art/a.png")
    side = Path(c["mine_path"])
    assert side.parent == scopes.plugin_dir({"account": pc.account, "id": SPACE}, PLUGIN) / "conflicts"
    assert side.read_bytes() != b"mine"  # sealed at rest, like every copy
    assert data_crypto.open_bytes(side.read_bytes(), pc.account) == b"mine"
    assert vcs_engine.resolve(pc.account, SPACE, PLUGIN, "asset", "art/a.png", "theirs") == {"ok": True}
    assert not side.exists() and pc.row("art/a.png", "asset")["sync_state"] == ""
    assert pc.data.get_file("art/a.png") == b"theirs"


def test_incoming_file_past_max_path_lands(pc: PC, ben: Ben) -> None:
    """Island Media Kit's 24 stuck files: a key under its folders plus the download's temp
    name ran past Windows' 260 characters, so every try failed with FileNotFoundError."""
    from backend.uefn_plugins import team_folders

    key = "media/" + "p" * 120 + "/0-" + hashlib.sha256(b"thumb").hexdigest() + ".bin"
    target = scopes.asset_file({"account": pc.account, "id": SPACE}, PLUGIN, key)
    assert len(team_folders._plain(target)) + len(".12345678.tmp") > 300
    if os.name == "nt":  # works whatever LongPathsEnabled or the EXE's manifest say (the app's isn't long-path aware)
        assert str(target).startswith("\\\\?\\")
    ben.put_file(key, b"\x89PNG-thumb")
    out = pc.run()
    assert (out["state"], out["pulled"]) == ("ok", 1)
    assert pc.data.get_file(key) == b"\x89PNG-thumb"
    assert (pc.row(key, "asset")["rev"], space_vcs.blocked(pc.account, SPACE, PLUGIN)) == (1, [])
    assert not [p for p in target.parent.iterdir() if p.name.endswith(".tmp")]
    pc.data.put_file(key, b"\x89PNG-mine")  # a local write past MAX_PATH too, and it goes out
    assert pc.run()["pushed"] == 1 and ben.server.space(SPACE).repo(PLUGIN).items[("asset", key)].content == b"\x89PNG-mine"


def test_an_edit_against_a_delete_waits_for_the_person(pc: PC, ben: Ben) -> None:
    pc.data.put("card.pip", {"hp": 1})
    pc.data.put("card.zed", {"hp": 1})
    pc.run()
    pip_gone = ben.delete("doc", "card.pip")
    ben.delete("doc", "card.zed")
    pc.data.put("card.pip", {"hp": 5})
    pc.data.put("card.zed", {"hp": 6})
    out = pc.run()
    assert out["conflicts"] == 2 and out["pushed"] == 0
    r = pc.row("card.pip")
    assert (r["sync_state"], r["dirty"], r["rev"]) == ("conflict", 1, pip_gone)
    assert "Ben deleted this" in r["sync_error"] and pc.data.get("card.pip") == {"hp": 5}
    assert space_vcs.conflict_get(pc.account, SPACE, PLUGIN, "doc", "card.pip")["reason"] == "deleted"

    vcs_engine.resolve(pc.account, SPACE, PLUGIN, "doc", "card.pip", "mine")  # Restore mine
    vcs_engine.resolve(pc.account, SPACE, PLUGIN, "doc", "card.zed", "theirs")  # Accept delete
    assert pc.run()["pushed"] == 1
    assert ben.doc("card.pip") == {"hp": 5} and ben.doc("card.zed") is None
    assert pc.row("card.zed") is None and pc.data.get("card.zed") is None


def test_a_change_deleted_on_the_server_while_deleted_here_settles(pc: PC, ben: Ben) -> None:
    pc.data.put("card.pip", {"hp": 1})
    pc.run()
    ben.delete("doc", "card.pip")
    pc.data.delete("card.pip")
    out = pc.run()
    assert out["conflicts"] == 0 and pc.row("card.pip") is None and ben.head() == 2


def test_a_delete_here_against_their_edit_keeps_theirs_until_the_person_picks(pc: PC, ben: Ben) -> None:
    pc.data.put("card.pip", {"hp": 1})
    pc.run()
    ben.put_doc("card.pip", {"hp": 7})
    pc.data.delete("card.pip")
    assert pc.run()["conflicts"] == 1
    assert pc.data.get("card.pip") == {"hp": 7}
    assert space_vcs.conflict_get(pc.account, SPACE, PLUGIN, "doc", "card.pip")["reason"] == "mine_deleted"
    vcs_engine.resolve(pc.account, SPACE, PLUGIN, "doc", "card.pip", "mine")  # delete it again
    assert pc.run()["pushed"] == 1 and ben.doc("card.pip") is None and pc.row("card.pip") is None


# --------------------------------------------------------------------------- crashes and races


class _LostAnswer:
    """The commit lands on the server, the answer never comes back."""

    def __init__(self, inner: Any) -> None:
        self.inner = inner

    def __getattr__(self, name: str) -> Any:
        return getattr(self.inner, name)

    def push(self, space: str, plugin: str, cs: Any) -> Any:
        self.inner.push(space, plugin, cs)
        return Offline("the answer got lost")


def test_a_crash_between_upload_and_commit_replays_and_loses_no_later_edit(pc: PC, ben: Ben) -> None:
    pc.data.put("card.pip", {"hp": 1})
    out = pc.run(backend=_LostAnswer(pc.backend))
    assert out["state"] == "offline" and ben.head() == 1  # it did land
    pending = space_vcs.cs_open(pc.account, SPACE, PLUGIN)
    assert len(pending) == 1 and pending[0]["state"] == "pushing" and pc.row("card.pip")["dirty"] == 1
    pc.data.put("card.pip", {"hp": 2})  # edited before the next round
    out = pc.run()
    assert out["state"] == "ok"
    space = ben.server.space(SPACE)
    assert pending[0]["cs_id"] in space.csids  # the same changeset, answered as a replay
    assert ben.head() == 2 and ben.doc("card.pip") == {"hp": 2}  # the later edit went out on top
    assert [json.loads(v.content) for v in space.repo(PLUGIN).versions[("doc", "card.pip")]] == [{"hp": 1}, {"hp": 2}]
    r = pc.row("card.pip")
    assert (r["dirty"], r["rev"]) == (0, 2) and space_vcs.cs_open(pc.account, SPACE) == []


def test_a_changeset_written_before_a_crash_goes_out_once_with_its_id(pc: PC, ben: Ben) -> None:
    pc.data.put("card.pip", {"hp": 1})
    ctx = vcs_engine.Ctx(pc.account, SPACE, PLUGIN, pc.backend)
    assert vcs_engine._build(ctx, now=time.time()) == 1  # written, then the app died before the upload
    cs_id = space_vcs.cs_open(pc.account, SPACE, PLUGIN)[0]["cs_id"]
    assert pc.run()["pushed"] == 1
    assert ben.server.space(SPACE).csids == {cs_id: (PLUGIN, 1)} and ben.head() == 1


def test_a_writer_in_another_process_racing_the_apply_loses_nothing(pc: PC, ben: Ben, world: conf.FakeWorld) -> None:
    pc.data.put("card.pip", {"hp": 1, "name": "Pip"})
    pc.run()
    ben.put_doc("card.pip", {"hp": 1, "name": "Pipo"})
    raw = scopes.encode_doc({"hp": 5, "name": "Pip"})
    sealed = data_crypto.seal_text(raw.decode(), pc.account)
    code = (
        "import sys\n"
        "sys.path.insert(0, sys.argv[1])\n"
        "from backend.store.repos import plugin_data\n"
        "a = sys.argv[2:]\n"
        "plugin_data.put(a[0], a[1], a[2], 'doc', a[3], value=a[4], size=int(a[5]), sha256=a[6], dirty=True)\n"
    )

    def bridge_writes_during_the_download(space: str, plugin: str, kind: str, key: str, seq: Any) -> None:
        world.server.hooks.pop("fetch", None)
        subprocess.run([sys.executable, "-c", code, APP_DIR, pc.account, SPACE, PLUGIN, "card.pip", sealed,
                        str(len(raw)), hashlib.sha256(raw).hexdigest()], check=True, env=dict(os.environ),
                       timeout=120)

    world.server.hooks["fetch"] = bridge_writes_during_the_download
    out = pc.run()
    assert "fetch" not in world.server.hooks and out["state"] == "ok"
    assert pc.data.get("card.pip") == {"hp": 5, "name": "Pipo"}  # the bridge's edit and Ben's, merged
    assert ben.doc("card.pip") == {"hp": 5, "name": "Pipo"}


def test_a_file_written_on_another_connection_during_the_download_is_kept(pc: PC, ben: Ben,
                                                                          world: conf.FakeWorld) -> None:
    pc.data.put_file("art/a.png", b"v1")
    pc.run()
    ben.put_file("art/a.png", b"theirs")

    def write_on_another_connection(*_: Any) -> None:
        world.server.hooks.pop("fetch", None)
        worker = threading.Thread(target=lambda: PluginData(PLUGIN).put_file("art/a.png", b"mine, late"))
        worker.start()
        worker.join(30)

    world.server.hooks["fetch"] = write_on_another_connection
    pc.run()
    assert pc.data.get_file("art/a.png") == b"theirs"
    c = space_vcs.conflict_get(pc.account, SPACE, PLUGIN, "asset", "art/a.png")
    assert data_crypto.open_bytes(Path(c["mine_path"]).read_bytes(), pc.account) == b"mine, late"


def test_a_busy_writer_during_a_long_pull_loses_nothing(pc: PC, ben: Ben) -> None:
    for n in range(30):
        ben.put_doc(f"card.b{n}", {"n": n})
    errors: list[BaseException] = []

    def write() -> None:
        try:
            for n in range(30):
                PluginData(PLUGIN).put(f"card.w{n}", {"n": n})
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    worker = threading.Thread(target=write)
    worker.start()
    pc.run(push=False)
    worker.join(60)
    assert not errors
    pc.run()
    assert pc.run()["state"] == "ok"
    for n in range(30):
        assert pc.data.get(f"card.b{n}") == {"n": n} and pc.data.get(f"card.w{n}") == {"n": n}
        assert ben.doc(f"card.w{n}") == {"n": n}
    assert repo.count_dirty(pc.account, SPACE) == 0


# --------------------------------------------------------------------------- resync, purge, groups, blocked


def test_a_pull_below_what_the_server_keeps_resyncs_every_item(pc: PC, ben: Ben, world: conf.FakeWorld) -> None:
    for key in ("card.keep", "card.gone", "card.extra"):
        pc.data.put(key, {"v": 1})
    pc.data.put("card.mine", {"a": 1, "b": 1})
    pc.run()
    ben.put_doc("card.keep", {"v": 2})
    ben.delete("doc", "card.gone")
    ben.put_doc("card.new", {"v": 1})
    ben.put_doc("card.mine", {"a": 2, "b": 1})
    world.purge(SPACE, PLUGIN, "doc", "card.extra")
    world.trim(SPACE, PLUGIN, 5)
    pc.data.put("card.mine", {"a": 1, "b": 3})
    out = pc.run()
    assert out["state"] == "ok"
    assert pc.data.get("card.keep") == {"v": 2} and pc.data.get("card.new") == {"v": 1}
    assert pc.row("card.gone") is None and pc.row("card.extra") is None
    assert pc.data.get("card.mine") == {"a": 2, "b": 3} and ben.doc("card.mine") == {"a": 2, "b": 3}
    assert space_vcs.head_get(pc.account, SPACE, PLUGIN)["seq"] == ben.head() == 6


def test_an_edit_of_an_item_purged_for_good_is_an_edit_against_a_delete(pc: PC, ben: Ben,
                                                                        world: conf.FakeWorld) -> None:
    pc.data.put("card.pip", {"hp": 1})
    pc.run()
    world.purge(SPACE, PLUGIN, "doc", "card.pip")
    pc.data.put("card.pip", {"hp": 2})
    out = pc.run()
    assert out["state"] == "ok" and ben.doc("card.pip") is None  # never made again silently
    r = pc.row("card.pip")
    assert (r["sync_state"], r["rev"]) == ("conflict", 0)
    vcs_engine.resolve(pc.account, SPACE, PLUGIN, "doc", "card.pip", "mine")
    assert pc.run()["pushed"] == 1 and ben.doc("card.pip") == {"hp": 2}


def test_a_group_waits_for_its_last_part(pc: PC, ben: Ben) -> None:
    ben.put_doc("card.a", {"v": 1}, group="g1", part=1, last=False)
    out = pc.run(push=False)
    assert out["pulled"] == 0 and pc.data.get("card.a") is None
    assert space_vcs.head_get(pc.account, SPACE, PLUGIN)["seq"] == 0
    ben.put_doc("card.b", {"v": 1}, group="g1", part=2, last=True)
    out = pc.run(push=False)
    assert out["pulled"] == 2 and pc.data.get("card.a") == {"v": 1} and pc.data.get("card.b") == {"v": 1}
    assert space_vcs.head_get(pc.account, SPACE, PLUGIN)["seq"] == 2


def test_a_bad_download_is_set_aside_and_the_cursor_moves_on(pc: PC, ben: Ben, world: conf.FakeWorld) -> None:
    ben.put_doc("card.a", {"v": 1})
    ben.put_doc("card.b", {"v": 1})
    world.server.bad.add((SPACE, PLUGIN, "doc", "card.a", 1))
    out = pc.run()
    assert out["state"] == "ok" and pc.data.get("card.b") == {"v": 1} and pc.data.get("card.a") is None
    assert space_vcs.head_get(pc.account, SPACE, PLUGIN)["seq"] == 2
    blocked = space_vcs.blocked(pc.account, SPACE, PLUGIN)
    assert [(b["key"], b["seq"]) for b in blocked] == [("card.a", 1)] and "verify" in blocked[0]["error"]
    assert space_vcs.unit_seqs(pc.account, SPACE, PLUGIN, ["doc:card.a"]) == {"doc:card.a": 0}
    pc.run()
    assert space_vcs.blocked(pc.account, SPACE, PLUGIN)[0]["attempts"] == 1  # still waiting its turn
    world.server.bad.clear()
    space_vcs.blocked_retry_now(pc.account, SPACE, PLUGIN, "doc", "card.a")
    pc.run()
    assert pc.data.get("card.a") == {"v": 1} and space_vcs.blocked(pc.account, SPACE) == []


def test_a_download_failing_the_same_way_backs_off_and_still_lands_later(pc: PC, ben: Ben,
                                                                          world: conf.FakeWorld) -> None:
    """The owner's 24 files showed 51 tries within minutes: every auto lock on their unit
    found this PC behind and tried the download again at once, past its backoff."""
    pc.data.put("card.pip", {"hp": 1})
    pc.run()
    ben.put_doc("card.pip", {"hp": 2})
    world.server.bad.add((SPACE, PLUGIN, "doc", "card.pip", 2))  # fails the same way every time
    pc.run()

    def tries() -> int:
        return int(space_vcs.blocked(pc.account, SPACE, PLUGIN)[0]["attempts"])

    assert tries() == 1
    for _ in range(10):  # the plugin locks the unit again and again (auto locks)
        assert not vcs_engine.acquire(pc.account, SPACE, PLUGIN, pc.backend, [("doc:card.pip", "card.pip")],
                                      reason="auto")["doc:card.pip"]["ok"]
        pc.run()
    assert tries() == vcs_engine.AUTO_RETRIES  # a few at once, then it waits its backoff
    b = space_vcs.blocked(pc.account, SPACE, PLUGIN)[0]
    assert b["next_try"] > time.time() + 60 and "verify" in b["error"]  # its reason still shows
    world.server.bad.clear()  # fixed (an update, the server)
    conn_now = b["next_try"] + 1
    pc.run(now=conn_now)  # its turn comes: it lands, nothing was dropped
    assert pc.data.get("card.pip") == {"hp": 2} and space_vcs.blocked(pc.account, SPACE) == []


# --------------------------------------------------------------------------- locks


def test_rows_under_someone_elses_lock_wait_and_go_after_it_is_released(pc: PC, ben: Ben) -> None:
    pc.data.put("card.pip", {"hp": 1})
    pc.run()
    ben.client.lock(SPACE, PLUGIN, [LockUnit("doc:card.pip", "card.pip")], "best", {"doc:card.pip": 1})
    pc.data.put("card.pip", {"hp": 2})
    pc.data.put("card.zed", {"hp": 1})
    out = pc.run()
    assert out["pushed"] == 1 and ben.doc("card.zed") == {"hp": 1} and ben.doc("card.pip") == {"hp": 1}
    r = pc.row("card.pip")
    assert (r["sync_state"], r["sync_error"]) == ("locked_out", "Locked by Ben")
    assert space_vcs.foreign_lock(pc.account, SPACE, PLUGIN, "doc:card.pip")["holder_name"] == "Ben"
    ben.client.unlock(SPACE, PLUGIN, ["doc:card.pip"])
    assert vcs_engine.refresh(pc.account, SPACE, [PLUGIN], pc.backend)["state"] == "ok"
    assert pc.row("card.pip")["sync_state"] == ""
    assert pc.run()["pushed"] == 1 and ben.doc("card.pip") == {"hp": 2}


def test_a_lock_on_a_unit_this_pc_is_behind_on_pulls_first(pc: PC, ben: Ben) -> None:
    pc.data.put("card.pip", {"hp": 1})
    pc.run()
    ben.put_doc("card.pip", {"hp": 2})
    got = vcs_engine.acquire(pc.account, SPACE, PLUGIN, pc.backend, [("doc:card.pip", "card.pip")])
    assert got["doc:card.pip"]["ok"] and got["doc:card.pip"]["state"] == "granted"
    assert pc.data.get("card.pip") == {"hp": 2}
    mine = space_vcs.locks(pc.account, SPACE, PLUGIN)
    assert [(lk["unit"], lk["mine"], lk["reason"]) for lk in mine] == [("doc:card.pip", True, "manual")]
    now = time.time()
    assert vcs_engine.renew_due(pc.account, SPACE, PLUGIN, active=True, now=now + 61) == ["doc:card.pip"]
    assert vcs_engine.renew_due(pc.account, SPACE, PLUGIN, active=False, now=now + 61) == []
    assert vcs_engine.idle_auto_locks(pc.account, SPACE, PLUGIN, now=now + 400) == []  # manual: kept
    theirs = vcs_engine.acquire(pc.account, SPACE, PLUGIN, ben.client, [("doc:card.pip", "card.pip")])
    assert theirs["doc:card.pip"]["state"] == "locked"
    assert vcs_engine.release(pc.account, SPACE, PLUGIN, pc.backend, ["doc:card.pip"])["ok"]
    assert space_vcs.locks(pc.account, SPACE) == []
    free = ben.client.lock(SPACE, PLUGIN, [LockUnit("doc:card.pip")], "best", {"doc:card.pip": 2})
    assert type(free["doc:card.pip"]).__name__ == "Granted"


class _AsksLocks:
    """Records what each lock call says this PC has of each unit."""

    def __init__(self, inner: Any) -> None:
        self.inner, self.asked = inner, []

    def __getattr__(self, name: str) -> Any:
        return getattr(self.inner, name)

    def lock(self, space: str, plugin: str, units: list[LockUnit], mode: str, have_seq: Any) -> Any:
        self.asked.append(dict(have_seq))
        return self.inner.lock(space, plugin, units, mode, have_seq)


def test_a_lock_says_the_units_own_applied_seq_and_a_behind_answer_pulls_then_asks_once_more(
        pc: PC, ben: Ben, world: conf.FakeWorld) -> None:
    pc.data.put("card.pip", {"hp": 1})
    pc.run()  # card.pip at 1
    ben.put_doc("card.zed", {"v": 1})
    pc.run()  # the head is at 2, card.pip's last change still 1
    ben.put_doc("card.pip", {"hp": 2})  # 3, not here yet
    asks = _AsksLocks(pc.backend)
    got = vcs_engine.acquire(pc.account, SPACE, PLUGIN, asks, [("doc:card.pip", "card.pip")])
    assert asks.asked == [{"doc:card.pip": 1}, {"doc:card.pip": 3}]  # never the head; after one pull, the new seq
    assert got["doc:card.pip"]["state"] == "granted" and pc.data.get("card.pip") == {"hp": 2}

    # A change of the unit set aside: the head moves past it, the unit's seq doesn't.
    ben.put_doc("card.a", {"v": 1})  # 4
    world.server.bad.add((SPACE, PLUGIN, "doc", "card.a", 4))
    pc.run()
    assert space_vcs.head_get(pc.account, SPACE, PLUGIN)["seq"] == 4
    asks.asked.clear()
    got = vcs_engine.acquire(pc.account, SPACE, PLUGIN, asks, [("doc:card.a", "card.a")])
    assert asks.asked == [{"doc:card.a": 0}, {"doc:card.a": 0}]  # asked once more after the pull, still behind
    assert (got["doc:card.a"]["state"], got["doc:card.a"]["by"]) == ("behind", "Ben")
    assert got["doc:card.a"]["error"] == "Ben changed this after your copy. Sync first."
    world.server.bad.clear()
    asks.asked.clear()
    got = vcs_engine.acquire(pc.account, SPACE, PLUGIN, asks, [("doc:card.a", "card.a")])
    assert asks.asked == [{"doc:card.a": 0}, {"doc:card.a": 4}]  # its download is tried again at once
    assert got["doc:card.a"]["state"] == "granted" and pc.data.get("card.a") == {"v": 1}


def test_an_own_commit_past_a_gap_claims_its_units_only_once_the_gap_is_pulled(pc: PC, ben: Ben) -> None:
    pc.data.put("card.pip", {"hp": 1})
    pc.run()
    ben.put_doc("card.zed", {"v": 1})  # 2: not here when mine goes out
    pc.data.put("card.pip", {"hp": 2})
    out = pc.run(pull=False, push=True)
    assert out["pushed"] == 1 and ben.head() == 3
    assert space_vcs.head_get(pc.account, SPACE, PLUGIN)["seq"] == 1
    assert space_vcs.unit_seqs(pc.account, SPACE, PLUGIN, ["doc:card.pip"]) == {"doc:card.pip": 1}
    pc.run()
    assert space_vcs.unit_seqs(pc.account, SPACE, PLUGIN, ["doc:card.pip", "doc:card.zed"]) == {
        "doc:card.pip": 3, "doc:card.zed": 2}


def test_a_round_that_sent_changes_tells_the_live_group_to_pull(pc: PC, monkeypatch) -> None:
    from backend.uefn_plugins import live

    told: list[tuple[str, str, str]] = []
    monkeypatch.setattr(live, "committed", lambda a, s, p, throttle=False: told.append((a, s, p)))
    pc.data.put("card.pip", {"hp": 1})
    assert pc.run()["pushed"] == 1
    assert set(told) == {(pc.account, SPACE, PLUGIN)}
    sent = len(told)
    pc.run()  # nothing to send: nothing to tell
    assert len(told) == sent


def test_the_live_group_hears_of_a_commit_as_it_lands_not_after_the_whole_round(pc: PC, ben: Ben, monkeypatch) -> None:
    from backend.uefn_plugins import live

    told: list[tuple[bool, int]] = []
    monkeypatch.setattr(live, "committed", lambda a, s, p, throttle=False: told.append((throttle, ben.head())))
    pc.data.put("card.pip", {"hp": 1})
    assert pc.run()["pushed"] == 1
    # As the changeset committed (from the push loop), and once more when the round was done.
    assert told == [(True, 1), (False, 1)]


def test_auto_locks_go_30_s_after_their_last_change_went_out(pc: PC) -> None:
    pc.data.put("card.pip", {"hp": 1})
    vcs_engine.acquire(pc.account, SPACE, PLUGIN, pc.backend, [("doc:card.pip", "card.pip")], reason="auto")
    pc.run()
    now = time.time()
    assert vcs_engine.AUTO_RELEASE_S == 30.0
    assert vcs_engine.idle_auto_locks(pc.account, SPACE, PLUGIN, now=now + 20) == []
    assert vcs_engine.release_idle(pc.account, SPACE, PLUGIN, pc.backend, now=now + 31) == ["doc:card.pip"]
    assert space_vcs.locks(pc.account, SPACE) == []


def test_auto_locks_held_when_the_tab_closed_go_ten_seconds_later(pc: PC, monkeypatch) -> None:
    now = time.time()
    closed = {"at": 0.0}
    monkeypatch.setattr(vcs_engine, "_tab_closed_at", lambda plugin: closed["at"])
    pc.data.put("card.pip", {"hp": 1})
    vcs_engine.acquire(pc.account, SPACE, PLUGIN, pc.backend, [("doc:card.pip", "card.pip")], reason="auto", now=now)
    pc.run()
    assert vcs_engine.idle_auto_locks(pc.account, SPACE, PLUGIN, now=now + 20) == []  # the tab is open
    closed["at"] = now + 15
    assert vcs_engine.idle_auto_locks(pc.account, SPACE, PLUGIN, now=now + 18) == []  # a re-layout reopens it
    # A ducky working with no tab open: its lock, taken after the close, keeps the 30 s.
    pc.data.put("card.zed", {"v": 1})
    vcs_engine.acquire(pc.account, SPACE, PLUGIN, pc.backend, [("doc:card.zed", "card.zed")], reason="auto",
                       now=now + 16)
    pc.data.put("card.pip", {"hp": 2})  # not sent yet: kept until it is
    assert vcs_engine.idle_auto_locks(pc.account, SPACE, PLUGIN, now=now + 20) == []
    pc.run()
    assert vcs_engine.release_idle(pc.account, SPACE, PLUGIN, pc.backend, now=now + 27) == ["doc:card.pip"]
    assert [lk["unit"] for lk in space_vcs.locks(pc.account, SPACE, now=now + 27)] == ["doc:card.zed"]


def test_a_broken_lock_turns_unsent_edits_into_a_conflict(pc: PC, ben: Ben, world: conf.FakeWorld) -> None:
    pc.data.put("card.pip", {"hp": 1})
    pc.run()
    vcs_engine.acquire(pc.account, SPACE, PLUGIN, pc.backend, [("doc:card.pip", "card.pip")])
    pc.data.put("card.pip", {"hp": 9})
    boss = world.client("u_olga", "d_olga", name="Olga", admin=True)
    assert type(boss.break_lock(SPACE, PLUGIN, "doc:card.pip")).__name__ == "Done"
    ben.client.lock(SPACE, PLUGIN, [LockUnit("doc:card.pip", "card.pip")], "best", {"doc:card.pip": 1})
    vcs_engine.refresh(pc.account, SPACE, [PLUGIN], pc.backend)
    r = pc.row("card.pip")
    assert (r["sync_state"], r["dirty"]) == ("conflict", 1) and "Ben" in r["sync_error"]
    c = space_vcs.conflict_get(pc.account, SPACE, PLUGIN, "doc", "card.pip")
    assert c["reason"] == "lock_broken" and _mine(c, pc.account) == {"hp": 9}
    assert pc.run()["pushed"] == 0
    assert vcs_engine.resolve(pc.account, SPACE, PLUGIN, "doc", "card.pip", "theirs", pc.backend)["ok"]
    assert pc.data.get("card.pip") == {"hp": 1} and pc.row("card.pip")["dirty"] == 0


# --------------------------------------------------------------------------- answers and modes


def test_refused_items_wait_with_the_reason_and_the_rest_go_out(pc: PC, ben: Ben, world: conf.FakeWorld) -> None:
    world.set_max_file_bytes(10)
    pc.data.put_file("big.bin", b"x" * 64)
    pc.data.put("card.pip", {"hp": 1})
    out = pc.run()
    assert out["pushed"] == 1 and ben.doc("card.pip") == {"hp": 1}
    r = pc.row("big.bin", "asset")
    assert (r["sync_state"], r["sync_error"], r["dirty"]) == ("refused", conf.TOO_BIG, 1)


def test_offline_backs_off_and_a_forced_round_goes_anyway(pc: PC, ben: Ben, world: conf.FakeWorld) -> None:
    world.server.offline = True
    pc.data.put("card.pip", {"hp": 1})
    out = pc.run()
    assert out["state"] == "offline"
    head = space_vcs.head_get(pc.account, SPACE, PLUGIN)
    assert head["state"] == "offline" and head["next_try"] > time.time() + 20
    assert vcs_engine.run(pc.account, SPACE, PLUGIN, pc.backend)["skipped"]
    world.server.offline = False
    out = pc.run()
    assert (out["state"], out["pushed"]) == ("ok", 1)
    assert space_vcs.head_get(pc.account, SPACE, PLUGIN)["attempts"] == 0


class _Rejects:
    """The server answers every changeset that carries ``key`` with an error, a request it
    will never take (``tries`` counts them); everything else goes as usual."""

    def __init__(self, inner: Any, key: str) -> None:
        self.inner, self.key, self.tries = inner, key, 0

    def __getattr__(self, name: str) -> Any:
        return getattr(self.inner, name)

    def push(self, space: str, plugin: str, cs: Any) -> Any:
        if any(i.key == self.key for i in cs.items):
            self.tries += 1
            return Error("bad request: ops[8]: by must be an integer")
        return self.inner.push(space, plugin, cs)


def _store_api() -> Any:
    """The drawer's Retry and Drop my change (panel_api_store loads with panel_api)."""
    from frontend.ui_web import panel_api  # noqa: F401
    from frontend.ui_web import panel_api_store

    return panel_api_store


def _waits_over(account: str) -> None:
    """Every row's wait after a failed try is over, as if the backoff had passed."""
    from backend.store import db

    conn = db.connect()
    with db.write_txn(conn):
        conn.execute("UPDATE plugin_data SET next_try=0 WHERE account_id=? AND scope_id=?", (account, SPACE))


class _Pushes:
    """Counts push calls (``calls``); after ``limit`` of them the server answers every one
    with an error, as a rate limit or a broken server does."""

    def __init__(self, inner: Any, limit: int = 10 ** 9) -> None:
        self.inner, self.limit, self.calls = inner, limit, 0

    def __getattr__(self, name: str) -> Any:
        return getattr(self.inner, name)

    def push(self, space: str, plugin: str, cs: Any) -> Any:
        self.calls += 1
        if self.calls > self.limit:
            return Error("collect rate limit exceeded")
        return self.inner.push(space, plugin, cs)


def test_a_changeset_the_server_keeps_failing_waits_alone_and_later_changes_go(pc: PC, ben: Ben) -> None:
    panel_api_store = _store_api()
    pc.run()  # on the new sync: each call's writes go as one changeset
    rejects = _Rejects(pc.backend, "big.bin")
    with scopes.write_group(PLUGIN, message="Tool put_file"):
        PluginData(PLUGIN).put_file("big.bin", b"x" * 64)
    out = pc.run(backend=rejects)
    # Alone, its error may be the server refusing everything: no try counts, the plugin backs off.
    assert (out["state"], out["pushed"], rejects.tries) == ("error", 0, 1) and "by must be an integer" in out["error"]
    assert space_vcs.head_get(pc.account, SPACE, PLUGIN)["next_try"] > time.time()
    assert (pc.row("big.bin", "asset")["attempts"], pc.row("big.bin", "asset")["dirty"]) == (0, 1)
    assert [cs["state"] for cs in space_vcs.cs_open(pc.account, SPACE)] == ["pending"]

    def with_a_card(n: int) -> dict[str, Any]:
        with scopes.write_group(PLUGIN, message="Tool put_docs"):
            PluginData(PLUGIN).put(f"card.{n}", {"hp": n})
        return pc.run(backend=rejects)

    out = with_a_card(1)  # the server takes another changeset in the round: the error is the file's own
    assert (out["state"], out["pushed"], rejects.tries) == ("ok", 1, 2) and ben.doc("card.1") == {"hp": 1}
    head = space_vcs.head_get(pc.account, SPACE, PLUGIN)
    assert (head["state"], head["next_try"]) == ("ok", 0.0)  # the plugin doesn't back off for it
    r = pc.row("big.bin", "asset")
    assert (r["dirty"], r["attempts"], r["sync_state"]) == (1, 1, "") and r["next_try"] > time.time()
    assert "by must be an integer" in r["sync_error"] and space_vcs.cs_open(pc.account, SPACE) == []
    assert (with_a_card(2)["pushed"], rejects.tries) == (1, 2)  # the file waits its turn

    for n in range(2, repo.BLOCK_AFTER + 1):  # each try after its wait counts; the last one blocks it
        _waits_over(pc.account)
        assert with_a_card(n + 1)["state"] == "ok" and rejects.tries == n + 1
    r = pc.row("big.bin", "asset")
    assert (r["sync_state"], r["attempts"], r["dirty"]) == ("blocked", repo.BLOCK_AFTER, 1)
    _waits_over(pc.account)
    assert pc.run(backend=rejects)["state"] == "ok"
    assert rejects.tries == repo.BLOCK_AFTER + 1  # held until the person picks
    shown = vcs_engine.status(pc.account, SPACE, PLUGIN)["outgoing"]
    assert [(o["key"], o["state"]) for o in shown] == [("big.bin", "blocked")]
    assert "by must be an integer" in shown[0]["reason"]

    assert panel_api_store._retry_held(PLUGIN, "big.bin") == {"ok": True, "retried": 1}
    assert pc.run()["pushed"] == 1  # the server takes it now
    assert ben.server.space(SPACE).repo(PLUGIN).items[("asset", "big.bin")].content == b"x" * 64
    r = pc.row("big.bin", "asset")
    assert (r["dirty"], r["attempts"], r["sync_state"], r["sync_error"]) == (0, 0, "", "")


def test_a_blocked_change_can_be_dropped_and_the_teams_version_comes_back(pc: PC, ben: Ben,
                                                                         monkeypatch: pytest.MonkeyPatch) -> None:
    panel_api_store = _store_api()
    monkeypatch.setattr(repo, "BLOCK_AFTER", 1)
    pc.data.put_file("big.bin", b"v1")
    pc.run()
    rejects = _Rejects(pc.backend, "big.bin")
    with scopes.write_group(PLUGIN, message="Tool put_file"):
        PluginData(PLUGIN).put_file("big.bin", b"v2" * 64)
    with scopes.write_group(PLUGIN, message="Tool put_docs"):
        PluginData(PLUGIN).put("card.pip", {"hp": 1})  # it lands: the file's error is its own
    assert pc.run(backend=rejects)["state"] == "ok"
    r = pc.row("big.bin", "asset")
    assert (r["sync_state"], r["dirty"]) == ("blocked", 1) and "by must be an integer" in r["sync_error"]
    assert panel_api_store._drop_change(PLUGIN, "asset", "big.bin") == {"ok": True, "dropped": 1}
    assert pc.run()["state"] == "ok"
    assert pc.data.get_file("big.bin") == b"v1" and pc.row("big.bin", "asset")["dirty"] == 0
    assert rejects.tries == 1 and ben.head() == 2


def test_rows_of_no_write_group_that_fail_together_go_again_in_halves(pc: PC, ben: Ben) -> None:
    rejects = _Rejects(pc.backend, "big.bin")
    pc.data.put_file("big.bin", b"x" * 64)
    pc.data.put("card.pip", {"hp": 1})  # no write group: both go in one changeset first
    out = pc.run(backend=rejects)
    assert (out["state"], out["pushed"], rejects.tries) == ("ok", 1, 2)  # together, then the file alone
    assert ben.doc("card.pip") == {"hp": 1} and pc.row("card.pip")["dirty"] == 0
    r = pc.row("big.bin", "asset")
    assert (r["dirty"], r["attempts"], r["sync_state"]) == (1, 1, "")


def test_halving_finds_one_bad_row_among_many_within_the_servers_rate_limit(pc: PC, ben: Ben) -> None:
    for i in range(200):
        pc.data.put(f"card.{i:03d}", {"v": i})  # before the move: no write groups
    pushes = _Pushes(_Rejects(pc.backend, "card.150"), limit=60)  # 60 commits a minute, as DuckyOS allows
    out = pc.run(backend=pushes)
    assert (out["state"], pushes.calls <= 2 * 8 + 1) == ("ok", True)  # about 2·log2(200) pushes, not 200
    assert out["pushed"] == ben.head() and ben.head() <= 9  # the good rows land in a few commits
    tried = [r["key"] for r in space_vcs.plugin_rows(pc.account, SPACE, PLUGIN) if int(r["attempts"])]
    dirty = [r["key"] for r in space_vcs.plugin_rows(pc.account, SPACE, PLUGIN) if int(r["dirty"])]
    assert tried == dirty == ["card.150"]  # only the row the server won't take counts a try
    assert ben.doc("card.149") == {"v": 149} and ben.doc("card.150") is None


def test_an_error_to_every_changeset_backs_off_counts_no_try_and_all_goes_once_the_server_takes_it(
        pc: PC, ben: Ben) -> None:
    for i in range(120):
        pc.data.put(f"card.{i:03d}", {"v": i})  # before the move: one changeset of no write group
    refusing = _Pushes(pc.backend, limit=0)
    out = pc.run(backend=refusing)
    assert (out["state"], out["pushed"], refusing.calls) == ("error", 0, 3)  # all of it, then both halves
    assert out["error"] == "collect rate limit exceeded"
    head = space_vcs.head_get(pc.account, SPACE, PLUGIN)
    assert head["state"] == "error" and head["next_try"] > time.time()
    assert vcs_engine.run(pc.account, SPACE, PLUGIN, refusing)["skipped"]  # it waits out the backoff
    rows = space_vcs.plugin_rows(pc.account, SPACE, PLUGIN)
    assert len(rows) == 120 and all(int(r["dirty"]) and not int(r["attempts"]) and not r["sync_state"] for r in rows)
    assert (pc.run()["pushed"], ben.head()) == (4, 4)  # the server takes it now: every quarter lands
    assert ben.doc("card.000") == {"v": 0} and ben.doc("card.119") == {"v": 119}

    for i in range(3):
        with scopes.write_group(PLUGIN, message=f"Tool put {i}"):
            PluginData(PLUGIN).put(f"deck.{i}", {"v": i})
    refusing.calls = 0
    out = pc.run(backend=refusing)
    assert (out["state"], refusing.calls) == ("error", 2)  # two in a row with nothing taken: the round ends
    assert [int(pc.row(f"deck.{i}")["attempts"]) for i in range(3)] == [0, 0, 0]
    assert pc.run()["pushed"] == 3


class _AnswerLost:
    """The server commits, but its answer never arrives (a timeout)."""

    def __init__(self, inner: Any) -> None:
        self.inner = inner

    def __getattr__(self, name: str) -> Any:
        return getattr(self.inner, name)

    def push(self, space: str, plugin: str, cs: Any) -> Any:
        self.inner.push(space, plugin, cs)
        return Offline("timed out")


def test_an_error_to_a_resend_keeps_it_so_its_lost_commit_is_answered_never_pulled_back(pc: PC, ben: Ben) -> None:
    pc.run()
    with scopes.write_group(PLUGIN, message="Tool put_docs"):
        PluginData(PLUGIN).put("card.pip", {"hp": 1})
    assert pc.run(backend=_AnswerLost(pc.backend))["state"] == "offline"
    assert ben.doc("card.pip") == {"hp": 1}  # it landed; this PC doesn't know
    pc.data.put("card.pip", {"hp": 2})  # edited meanwhile
    limited = _Pushes(pc.backend, limit=0)  # a rate limit answers before the server looks at the changeset
    out = pc.run(backend=limited)
    # The resend got no answer, but its commit came back in the pull under its id: settled as
    # mine, never a change of "a teammate" to merge. The later edit then went and was refused.
    assert (out["state"], limited.calls) == ("error", 2)
    assert space_vcs.conflicts(pc.account, SPACE) == [] and pc.row("card.pip")["attempts"] == 0
    assert "pushing" not in [cs["state"] for cs in space_vcs.cs_open(pc.account, SPACE)]
    assert (pc.row("card.pip")["dirty"], pc.row("card.pip")["rev"]) == (1, 1)
    out = pc.run()
    assert (out["state"], out["conflicts"]) == ("ok", 0) and space_vcs.conflicts(pc.account, SPACE) == []
    assert ben.doc("card.pip") == {"hp": 2} and pc.row("card.pip")["dirty"] == 0


class _NeverSent:
    """The first push dies before it reaches the server (no commit); later ones go."""

    def __init__(self, inner: Any) -> None:
        self.inner, self.sent = inner, False

    def __getattr__(self, name: str) -> Any:
        return getattr(self.inner, name)

    def push(self, space: str, plugin: str, cs: Any) -> Any:
        if not self.sent:
            self.sent = True
            return Offline("timed out")
        return self.inner.push(space, plugin, cs)


def test_a_resend_the_server_keeps_failing_never_holds_the_teams_changes(pc: PC, ben: Ben) -> None:
    """The Laptop's 24 incoming: every resend drew a storage 408, the round stopped before
    its pull, and nothing came in for hours."""
    pc.run()
    failing = _Pushes(pc.backend, limit=0)  # every resend: "S3 HEAD failed: HTTP 408"
    # A resend that never reached the server: the team's changes come in, it waits.
    with scopes.write_group(PLUGIN, message="Tool put_docs"):
        PluginData(PLUGIN).put("card.new", {"hp": 9})
    assert pc.run(backend=_NeverSent(pc.backend))["state"] == "offline"
    ben.put_doc("card.zed", {"hp": 3})
    ben.put_file("img/zed.png", b"Z")
    out = pc.run(backend=failing)
    assert (out["state"], failing.calls) == ("error", 1)
    assert pc.data.get("card.zed") == {"hp": 3} and pc.data.get_file("img/zed.png") == b"Z"  # they came in
    assert space_vcs.head_get(pc.account, SPACE, PLUGIN)["seq"] == ben.head() == 2
    assert [cs["state"] for cs in space_vcs.cs_open(pc.account, SPACE)] == ["pushing"]  # still to be answered
    assert pc.run()["state"] == "ok" and ben.doc("card.new") == {"hp": 9}
    # A resend whose commit landed but whose answer got lost: found in the pull, settled as mine.
    with scopes.write_group(PLUGIN, message="Tool put_docs"):
        PluginData(PLUGIN).put("card.pip", {"hp": 1})
    assert pc.run(backend=_AnswerLost(pc.backend))["state"] == "offline"
    pip_seq = ben.head()
    ben.put_doc("card.zed", {"hp": 4})
    failing.calls = 0
    out = pc.run(backend=failing)
    assert (out["state"], failing.calls) == ("ok", 1)  # its answer came with the pull: nothing left unanswered
    assert pc.data.get("card.zed") == {"hp": 4} and space_vcs.conflicts(pc.account, SPACE) == []
    assert (pc.row("card.pip")["dirty"], pc.row("card.pip")["rev"]) == (0, pip_seq)
    assert space_vcs.cs_open(pc.account, SPACE) == [] and ben.head() == pip_seq + 1


class _FailsOnce:
    """The server answers the first changeset ``pick`` picks with an error, then takes all."""

    def __init__(self, inner: Any, pick: Any) -> None:
        self.inner, self.pick, self.failed = inner, pick, 0

    def __getattr__(self, name: str) -> Any:
        return getattr(self.inner, name)

    def push(self, space: str, plugin: str, cs: Any) -> Any:
        if not self.failed and self.pick(cs):
            self.failed += 1
            return Error("bad request")
        return self.inner.push(space, plugin, cs)


def test_a_later_part_of_a_write_group_that_fails_goes_again_as_it_was_so_its_group_closes(
        pc: PC, ben: Ben, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(vcs_engine, "MAX_ITEMS", 2)
    pc.run()  # on the new sync
    with scopes.write_group(PLUGIN, message="Tool build_set"):
        for key in ("card.a", "card.b", "card.c"):
            PluginData(PLUGIN).put(key, {"v": 1})
    once = _FailsOnce(pc.backend, lambda cs: cs.part == 2)
    out = pc.run(backend=once)
    assert (out["state"], out["pushed"], once.failed) == ("error", 1, 1)  # part 1 landed: part 2 backs off
    assert [(cs["part"], cs["state"]) for cs in space_vcs.cs_open(pc.account, SPACE)] == [(2, "pushing")]
    assert [int(pc.row(k)["attempts"]) for k in ("card.a", "card.b", "card.c")] == [0, 0, 0]
    assert pc.run()["state"] == "ok"
    page = ben.client.pull(SPACE, PLUGIN, 0, 50)
    assert [(cs.part, cs.last) for cs in page.changesets] == [(1, False), (2, True)]
    assert len({cs.group for cs in page.changesets}) == 1 and ben.doc("card.c") == {"v": 1}


def test_a_failed_part_of_a_write_group_holds_its_other_parts_and_they_go_again_together(
        pc: PC, ben: Ben, monkeypatch: pytest.MonkeyPatch) -> None:
    panel_api_store = _store_api()
    monkeypatch.setattr(vcs_engine, "MAX_ITEMS", 2)
    monkeypatch.setattr(repo, "BLOCK_AFTER", 1)
    pc.run()  # on the new sync
    rejects = _Rejects(pc.backend, "big.bin")
    with scopes.write_group(PLUGIN, message="Tool build_set"):
        data = PluginData(PLUGIN)
        data.put_file("big.bin", b"x" * 64)  # in the first part
        data.put("card.a", {"v": 1})
        data.put("card.b", {"v": 1})  # in the second
    with scopes.write_group(PLUGIN, message="Tool put_docs"):
        PluginData(PLUGIN).put("card.z", {"v": 1})  # it lands: the group's error is its own
    out = pc.run(backend=rejects)
    assert (out["state"], out["pushed"], rejects.tries, ben.head()) == ("ok", 1, 1, 1)  # no part of the group
    rows = [pc.row("big.bin", "asset"), pc.row("card.a"), pc.row("card.b")]
    assert [(r["sync_state"], r["attempts"], r["dirty"]) for r in rows] == [("blocked", 1, 1)] * 3
    assert space_vcs.cs_open(pc.account, SPACE) == []
    assert panel_api_store._retry_held(PLUGIN, "card.b") == {"ok": True, "retried": 3}  # its group comes along
    assert pc.run()["pushed"] == 2
    assert ben.doc("card.a") == {"v": 1} and ben.doc("card.b") == {"v": 1}
    page = ben.client.pull(SPACE, PLUGIN, 1, 50)
    assert [(bool(cs.group), cs.part, cs.last) for cs in page.changesets] == [(True, 1, False), (True, 2, True)]
    assert len({cs.group for cs in page.changesets}) == 1


def test_a_failed_read_of_one_file_never_holds_up_the_rest(pc: PC, ben: Ben) -> None:
    pc.data.put_file("art/a.png", b"a")
    pc.data.put("card.pip", {"hp": 1})
    scopes.asset_file({"account": pc.account, "id": SPACE}, PLUGIN, "art/a.png").write_bytes(b"not sealed")
    out = pc.run()
    assert out["pushed"] == 1 and ben.doc("card.pip") == {"hp": 1}
    r = pc.row("art/a.png", "asset")
    assert (r["attempts"], r["dirty"]) == (1, 1) and r["next_try"] > time.time() and r["sync_error"]


def test_manual_mode_moves_only_when_asked(pc: PC, ben: Ben) -> None:
    vcs_engine.set_mode(pc.account, SPACE, PLUGIN, "manual")
    pc.data.put("card.pip", {"hp": 1})
    ben.put_doc("card.zed", {"hp": 1})
    out = pc.run()
    assert (out["pushed"], out["pulled"]) == (0, 0) and pc.data.get("card.zed") is None
    assert pc.run(pull=True)["pulled"] == 1 and pc.data.get("card.zed") == {"hp": 1}
    assert pc.run(push=True, message="Tuned the starter cards")["pushed"] == 1
    assert ben.client.history(SPACE, PLUGIN).entries[0].message == "Tuned the starter cards"
    with pytest.raises(ValueError):
        vcs_engine.set_mode(pc.account, SPACE, PLUGIN, "sometimes")


# --------------------------------------------------------------------------- workflows, callbacks, presence


def test_a_workflow_clash_keeps_your_copy_before_sync_and_folder_adds_keep_both(pc: PC, ben: Ben) -> None:
    from backend.automations import owned
    from backend.store.repos import automations as legacy

    wf = {"id": "wf1", "name": "Daily check", "graph": {"nodes": []}, "updated": 1.0}
    pc.put_raw(WF, "wf1", wf)
    pc.put_raw(WF, owned.FOLDERS_KEY, {"folders": ["Play tests"]})
    pc.run(WF)
    ben.put_doc("wf1", {**wf, "name": "Daily check (Ben)"}, plugin=WF)
    ben.put_doc(owned.FOLDERS_KEY, {"folders": ["Play tests", "Tycoon"]}, plugin=WF)
    pc.put_raw(WF, "wf1", {**wf, "name": "Daily check (Ana)", "updated": 2.0})
    pc.put_raw(WF, owned.FOLDERS_KEY, {"folders": ["Play tests", "Islands"]})
    out = pc.run(WF)
    assert out["conflicts"] == 1
    assert pc.get_raw(WF, "wf1")["name"] == "Daily check (Ben)"
    saved = [owned.open_version(text, pc.account) for _, text in legacy.version_rows("wf1")]
    assert [(v["name"], v["note"]) for v in saved] == [("Daily check (Ana)", owned.BEFORE_SYNC_NOTE)]
    assert pc.get_raw(WF, owned.FOLDERS_KEY) == {"folders": ["Play tests", "Tycoon", "Islands"]}
    assert ben.doc(owned.FOLDERS_KEY, plugin=WF) == {"folders": ["Play tests", "Tycoon", "Islands"]}


def test_on_change_tells_the_plugin_which_keys_a_pull_changed(pc: PC, ben: Ben,
                                                              monkeypatch: pytest.MonkeyPatch) -> None:
    heard: list[list[str]] = []
    pc.data.on_change(heard.append)
    ben.put_doc("card.a", {"v": 1})
    ben.put_file("img/a.png", b"A")
    pc.run()
    assert heard == [["card.a", "img/a.png"]]
    assert scopes.deliver_data_changes(PLUGIN) == 0  # already heard in this process
    # A pull in another process (the app, while this is the bridge): heard before the next tool call.
    with monkeypatch.context() as m:
        m.setattr(vcs_engine, "_deliver", lambda *a, **k: None)
        ben.put_doc("card.b", {"v": 1})
        pc.run()
    assert heard == [["card.a", "img/a.png"]]
    assert scopes.deliver_data_changes(PLUGIN) == 1 and heard[-1] == ["card.b"]
    assert scopes.deliver_data_changes(PLUGIN) == 0
    # A pull in the bridge (a lock it was behind on), then one here: this process hears both.
    with monkeypatch.context() as m:
        m.setattr(vcs_engine, "_deliver", lambda *a, **k: None)
        ben.put_doc("card.c", {"v": 1})
        pc.run()
    ben.put_doc("card.d", {"v": 1})
    pc.run()
    assert heard[-1] == ["card.c", "card.d"]
    scopes.forget_change_listeners(PLUGIN)
    assert scopes.deliver_data_changes(PLUGIN) == 0


def test_a_pull_names_the_keys_it_changed_and_never_this_pcs_own_items(pc: PC, ben: Ben,
                                                                       monkeypatch: pytest.MonkeyPatch) -> None:
    """Open panels merge the keys a pull changed. A teammate's own items under a
    ``data_local`` prefix (Island Media Kit's per-load diagnostics) are never news here:
    they used to reset every open panel on every PC."""
    from backend.uefn_plugins.sync_scheduler import synced_event

    monkeypatch.setattr(scopes, "local_prefixes", lambda plugin: ("qa.",) if plugin == PLUGIN else ())
    ben.put_doc("qa.boot.1", {"phase": "mounted"})
    out = pc.run()
    assert (out["state"], out["changed"], out["keys"]) == ("ok", [], [])
    ben.put_doc("card.a", {"v": 1})
    ben.put_file("img/a.png", b"A")
    out = pc.run()
    assert (out["changed"], out["keys"]) == ([PLUGIN], ["card.a", "img/a.png"])
    assert synced_event({"changed": [PLUGIN], "keys": {PLUGIN: out["keys"], "other": ["x"]}}) == {
        "type": "plugin_scope_changed", "plugins": [PLUGIN], "synced": True, "keys": {PLUGIN: ["card.a", "img/a.png"]}}
    assert synced_event({"changed": [], "state": "ok"}) == {"type": "plugin_scope_changed", "plugins": [], "synced": True}


def test_presence_and_the_drawer_name_a_doc_by_its_own_name(pc: PC, monkeypatch: pytest.MonkeyPatch) -> None:
    """"Editing Untitled island 1", never "Editing qa.panel.2de0…": a doc's name, title or
    label field names it, a file its file name; this PC's own items never show."""
    from backend.uefn_plugins import spaces

    monkeypatch.setattr(scopes, "local_prefixes", lambda plugin: ("qa.",) if plugin == PLUGIN else ())
    pc.put_raw(PLUGIN, "project.mvbtpazj", {"name": "Untitled island 1", "clips": []})
    pc.put_raw(PLUGIN, "seq.1", {"title": "  Trailer 1 "})
    pc.put_raw(PLUGIN, "card.zed", {"hp": 2})
    for unit in ("doc:project.mvbtpazj", "doc:seq.1", "doc:card.zed", "doc:qa.panel.2de0", "asset:img/art/logo.png"):
        space_vcs.activity_note(pc.account, SPACE, PLUGIN, unit)
    blob = vcs_engine.presence_blob(pc.account, SPACE, [PLUGIN])
    names = sorted(e["unitName"] for e in blob["plugins"][PLUGIN]["editing"])
    assert names == ["Trailer 1", "Untitled island 1", "card.zed", "logo.png"]
    pc.put_raw(PLUGIN, "project.mvbtpazj", {"name": "Crash Course"})
    assert vcs_engine.unit_label(pc.account, SPACE, PLUGIN, "doc:project.mvbtpazj") == "Crash Course"
    assert vcs_engine.unit_label(pc.account, SPACE, PLUGIN, "doc:gone") == "gone"
    outgoing = {o["key"]: o["name"] for o in spaces.team_fields(pc.account, SPACE, PLUGIN)["changes"]["outgoing"]}
    assert (outgoing["project.mvbtpazj"], outgoing["card.zed"]) == ("Crash Course", "card.zed")


def test_presence_says_who_edits_what_and_keeps_the_roster(pc: PC, ben: Ben, world: conf.FakeWorld) -> None:
    space_vcs.activity_note(pc.account, SPACE, PLUGIN, "doc:card.pip", actor="agent", ducky="Builder")
    assert vcs_engine.beat(pc.account, SPACE, pc.backend, [PLUGIN])["ok"]
    blob, _ = world.server.space(SPACE).presence[("u_ana", vcs_engine.device_id())]
    editing = blob["plugins"][PLUGIN]["editing"]
    assert [(e["unitName"], e["actor"], e["ducky"]) for e in editing] == [("card.pip", "agent", "Builder")]
    assert vcs_engine.beat(pc.account, SPACE, pc.backend, [PLUGIN])["skipped"]  # nothing changed
    ben.client.beat(SPACE, {"device": "Ben's PC"})
    assert vcs_engine.beat(pc.account, SPACE, pc.backend, [PLUGIN], force=True)["people"] == 2
    people = vcs_engine.status(pc.account, SPACE, PLUGIN)["people"]
    assert sorted(p["user_name"] for p in people) == ["Ana", "Ben"]


def test_history_names_each_pc_by_the_name_a_roster_or_the_change_gave(
        pc: PC, ben: Ben, monkeypatch: pytest.MonkeyPatch) -> None:
    from backend.uefn_plugins import spaces
    from backend.uefn_plugins.ledger import History, HistoryEntry

    ben.client.beat(SPACE, {"label": "Ben's PC"})
    assert vcs_engine.beat(pc.account, SPACE, pc.backend, [PLUGIN], force=True)["people"] == 2
    names = vcs_engine.device_names(pc.account, SPACE)
    assert names["d_ben"] == "Ben's PC" and names[vcs_engine.device_id()] == vcs_engine.device_label()
    # Ben's PC leaves the roster and keeps its name.
    kept = vcs_engine.with_devices(space_vcs.presence_get(pc.account, SPACE)["roster"], {"people": []})
    assert kept["devices"]["d_ben"] == "Ben's PC"

    monkeypatch.setattr(spaces, "_team", lambda plugin: (pc.account, SPACE, pc.backend))
    monkeypatch.setattr(pc.backend, "history", lambda *a, **k: History((
        HistoryEntry(seq=3, device="d_ben"),
        HistoryEntry(seq=2, device="d_lap", device_name="Ana's laptop"),
        HistoryEntry(seq=1, device="d_gone"),
    )))
    got = spaces.team_history(PLUGIN)["entries"]
    # The PC's name, never its id ("deviceId" keeps that): the change's own name, else a
    # roster's, else blank.
    assert [(e["device"], e["deviceId"]) for e in got] == [
        ("Ben's PC", "d_ben"), ("Ana's laptop", "d_lap"), ("", "d_gone")]


# --------------------------------------------------------------------------- restore, history, lost access


def test_restore_and_undo_are_new_changesets(pc: PC, ben: Ben) -> None:
    pc.data.put("card.pip", {"hp": 1})
    pc.run()
    pc.data.put("card.pip", {"hp": 2})
    pc.data.put("card.new", {"x": 1})
    pc.run()
    out = vcs_engine.undo(pc.account, SPACE, PLUGIN, pc.backend, 2)
    assert out["ok"] and out["seq"] == 3
    assert pc.data.get("card.pip") == {"hp": 1} and pc.data.get("card.new") is None
    out = vcs_engine.restore(pc.account, SPACE, PLUGIN, pc.backend, items={("doc", "card.pip"): 2})
    assert out["ok"] and pc.data.get("card.pip") == {"hp": 2}
    hist = vcs_engine.history(pc.account, SPACE, PLUGIN, pc.backend)
    assert [e["seq"] for e in hist["entries"]] == [4, 3, 2, 1] and hist["entries"][1]["message"] == "Undo #2"
    item = vcs_engine.history(pc.account, SPACE, PLUGIN, pc.backend, key=("doc", "card.pip"))
    assert [e["seq"] for e in item["entries"]] == [4, 3, 2, 1]


def test_unsent_changes_of_a_lost_space_can_be_saved_to_local(pc: PC) -> None:
    pc.data.put("card.pip", {"hp": 2})
    pc.data.put_file("img/a.png", b"mine")
    local = pc.data.personal()
    local.put("card.pip", {"hp": 1})
    assert space_vcs.pending_unsent(pc.account, SPACE) == 2
    out = vcs_engine.save_unsent_to_local(pc.account, SPACE)
    assert sorted((s["key"], s["localKey"]) for s in out["saved"]) == [
        ("card.pip", "card.pip-from-alpha"), ("img/a.png", "img/a.png")]
    assert local.get("card.pip") == {"hp": 1} and local.get("card.pip-from-alpha") == {"hp": 2}
    assert local.get_file("img/a.png") == b"mine"
    assert space_vcs.pending_unsent(pc.account, SPACE) == 0
    again = vcs_engine._local_key(pc.account, PLUGIN, "asset", "img/a.png", "alpha")
    assert again == "img/a-from-alpha.png"


# --------------------------------------------------------------------------- review fixes


def _side_files(pc: PC) -> list[Path]:
    folder = scopes.plugin_dir({"account": pc.account, "id": SPACE}, PLUGIN) / "conflicts"
    return sorted(folder.glob("*")) if folder.is_dir() else []


def _opened(pc: PC, path: str) -> bytes:
    return data_crypto.open_bytes(scopes.asset_file({"account": pc.account, "id": SPACE}, PLUGIN, path).read_bytes(),
                                  pc.account)


def test_a_file_held_open_is_set_aside_alone_and_its_files_go_back(pc: PC, ben: Ben,
                                                                   monkeypatch: pytest.MonkeyPatch) -> None:
    pc.data.put_file("art/a.png", b"v1")
    pc.run()
    ben.put_file("art/a.png", b"theirs")
    ben.put_file("art/b.png", b"B")
    ben.put_doc("card.c", {"v": 1})
    pc.data.put_file("art/a.png", b"mine")  # keep both: mine goes aside, then theirs lands
    target = scopes.asset_file({"account": pc.account, "id": SPACE}, PLUGIN, "art/a.png")
    real = vcs_engine._Files.move

    def held_open(self: Any, src: Path, dst: Path, *, keep: bool) -> None:
        if dst == target and src.name.endswith(".tmp"):
            raise PermissionError("[WinError 32] in use")
        real(self, src, dst, keep=keep)

    monkeypatch.setattr(vcs_engine._Files, "move", held_open)
    out = pc.run()
    assert out["state"] == "ok"
    assert pc.data.get_file("art/b.png") == b"B" and pc.data.get("card.c") == {"v": 1}
    assert space_vcs.head_get(pc.account, SPACE, PLUGIN)["seq"] == ben.head()
    blocked = space_vcs.blocked(pc.account, SPACE, PLUGIN)
    assert [b["key"] for b in blocked] == ["art/a.png"] and "open in another program" in blocked[0]["error"]
    r = pc.row("art/a.png", "asset")  # untouched: still mine, unsent, and the file is mine
    assert (r["dirty"], r["sha256"], r["sync_state"]) == (1, hashlib.sha256(b"mine").hexdigest(), "")
    assert _opened(pc, "art/a.png") == b"mine" and _side_files(pc) == []
    assert space_vcs.conflict_get(pc.account, SPACE, PLUGIN, "asset", "art/a.png") is None
    assert not list(target.parent.glob("*.old")) and not list(target.parent.glob("*.tmp"))
    monkeypatch.setattr(vcs_engine._Files, "move", real)
    space_vcs.blocked_retry_now(pc.account, SPACE, PLUGIN, "asset", "art/a.png")
    assert pc.run()["conflicts"] == 1
    assert pc.data.get_file("art/a.png") == b"theirs"
    c = space_vcs.conflict_get(pc.account, SPACE, PLUGIN, "asset", "art/a.png")
    assert data_crypto.open_bytes(Path(c["mine_path"]).read_bytes(), pc.account) == b"mine"


def test_an_apply_that_stops_puts_every_file_back(pc: PC, ben: Ben, monkeypatch: pytest.MonkeyPatch) -> None:
    pc.data.put_file("art/a.png", b"v1")
    pc.data.put("card.d", {"v": 1})
    pc.run()
    ben._push(PLUGIN, conf.put_item("asset", "art/a.png", b"theirs", base=1),  # one changeset: one transaction
              conf.put_item("doc", "card.d", scopes.encode_doc({"v": 2}), base=1))
    pc.data.put("card.d", {"v": 3})  # merged after the file landed: the data key goes missing there

    def no_key(*_: Any, **__: Any) -> None:
        raise data_crypto.Locked("no data key")

    monkeypatch.setattr(vcs_engine._Apply, "_merge_doc", no_key)
    assert pc.run()["state"] == "locked"
    assert _opened(pc, "art/a.png") == b"v1"
    assert pc.row("art/a.png", "asset")["sha256"] == hashlib.sha256(b"v1").hexdigest()
    folder = scopes.asset_file({"account": pc.account, "id": SPACE}, PLUGIN, "art/a.png").parent
    assert not list(folder.glob("*.old")) and not list(folder.glob("*.tmp"))
    assert space_vcs.head_get(pc.account, SPACE, PLUGIN)["seq"] == 1


def test_a_remote_delete_keeps_mine_of_an_unresolved_conflict(pc: PC, ben: Ben) -> None:
    pc.data.put("card.pip", {"hp": 1})
    pc.data.put_file("art/a.png", b"v1")
    pc.run()
    ben.put_doc("card.pip", {"hp": 3})
    ben.put_file("art/a.png", b"theirs")
    pc.data.put("card.pip", {"hp": 2})
    pc.data.put_file("art/a.png", b"mine")
    assert pc.run()["conflicts"] == 2  # theirs current, mine kept in the conflicts
    pip_gone = ben.delete("doc", "card.pip")
    file_gone = ben.delete("asset", "art/a.png")
    assert pc.run()["state"] == "ok"
    assert pc.data.get("card.pip") == {"hp": 2} and pc.data.get_file("art/a.png") == b"mine"
    for key, kind, gone in (("card.pip", "doc", pip_gone), ("art/a.png", "asset", file_gone)):
        r = pc.row(key, kind)
        assert (r["dirty"], r["sync_state"], r["rev"]) == (1, "conflict", gone)
        c = space_vcs.conflict_get(pc.account, SPACE, PLUGIN, kind, key)
        assert (c["reason"], c["theirs_deleted"]) == ("deleted", 1)
    assert _side_files(pc) == []  # the side copy is the file again
    vcs_engine.resolve(pc.account, SPACE, PLUGIN, "doc", "card.pip", "mine")  # Restore mine
    vcs_engine.resolve(pc.account, SPACE, PLUGIN, "asset", "art/a.png", "mine")
    assert pc.run()["pushed"] == 1
    assert ben.doc("card.pip") == {"hp": 2}
    assert ben.server.space(SPACE).repo(PLUGIN).items[("asset", "art/a.png")].content == b"mine"


def test_keep_mine_after_the_row_went_makes_it_again(pc: PC, ben: Ben) -> None:
    from backend.store import db

    pc.data.put("card.pip", {"hp": 1})
    pc.run()
    ben.put_doc("card.pip", {"hp": 3})
    pc.data.put("card.pip", {"hp": 2})
    pc.run()
    conn = db.connect()
    with db.write_txn(conn):
        space_vcs.delete_row(conn, pc.account, SPACE, PLUGIN, "doc", "card.pip")
    assert vcs_engine.resolve(pc.account, SPACE, PLUGIN, "doc", "card.pip", "mine") == {"ok": True}
    r = pc.row("card.pip")
    assert (r["dirty"], r["rev"]) == (1, 0) and pc.data.get("card.pip") == {"hp": 2}


def _v1_doc(pc: PC, key: str, value: Any, *, rev: int, dirty: bool) -> None:
    raw = scopes.encode_doc(value)
    repo.put(pc.account, SPACE, PLUGIN, "doc", key, value=data_crypto.seal_text(raw.decode(), pc.account),
             size=len(raw), sha256=hashlib.sha256(raw).hexdigest(), dirty=dirty, rev=rev)


def test_the_move_from_the_older_sync_reconciles_each_row(pc: PC, ben: Ben) -> None:
    from dataclasses import replace

    _v1_doc(pc, "card.same", {"v": "x"}, rev=7, dirty=False)
    _v1_doc(pc, "card.old", {"v": "y"}, rev=5, dirty=False)
    _v1_doc(pc, "card.mine", {"v": "edited here"}, rev=8, dirty=True)
    _v1_doc(pc, "card.stale", {"v": "stale edit"}, rev=3, dirty=True)
    _v1_doc(pc, "card.gone", {"v": 1}, rev=4, dirty=False)
    _v1_doc(pc, "card.edited_gone", {"v": "mine"}, rev=4, dirty=True)
    _v1_doc(pc, "card.new", {"v": "made here"}, rev=0, dirty=True)
    imported = [("card.same", {"v": "x"}, 7), ("card.old", {"v": "y2"}, 6), ("card.mine", {"v": "z"}, 8),
                ("card.stale", {"v": "t"}, 9), ("card.extra", {"v": "e"}, 2)]
    items = [replace(conf.put_item("doc", key, scopes.encode_doc(value)), meta={"v1rev": v1rev})
             for key, value, v1rev in imported]
    assert ben._push(PLUGIN, *items, message="Imported from team data") == 1
    out = pc.run()
    assert out["state"] == "ok"
    head = space_vcs.head_get(pc.account, SPACE, PLUGIN)
    assert head["engine"] == "v2" and head["seq"] == ben.head()
    same = pc.row("card.same")
    assert (same["dirty"], same["rev"]) == (0, 1)
    assert space_vcs.base_get(pc.account, SPACE, PLUGIN, "card.same")["seq"] == 1
    assert pc.data.get("card.old") == {"v": "y2"} and pc.row("card.old")["rev"] == 1
    assert pc.data.get("card.extra") == {"v": "e"}
    assert ben.doc("card.mine") == {"v": "edited here"}  # an edit on top of what was imported goes out
    assert ben.doc("card.new") == {"v": "made here"}
    assert pc.data.get("card.stale") == {"v": "t"} and pc.row("card.stale")["sync_state"] == "conflict"
    stale = space_vcs.conflict_get(pc.account, SPACE, PLUGIN, "doc", "card.stale")
    assert _mine(stale, pc.account) == {"v": "stale edit"} and ben.doc("card.stale") == {"v": "t"}
    assert pc.row("card.gone") is None
    gone = space_vcs.conflict_get(pc.account, SPACE, PLUGIN, "doc", "card.edited_gone")
    assert gone["reason"] == "deleted" and pc.row("card.edited_gone")["dirty"] == 1
    assert ben.doc("card.edited_gone") is None
    assert space_vcs.unit_seqs(pc.account, SPACE, PLUGIN, ["doc:card.same"]) == {"doc:card.same": 1}


class _CommitsMidListing:
    """Ben commits ``commit`` just before the full listing's second page is read."""

    def __init__(self, inner: Any, commit: Callable[[], Any]) -> None:
        self.inner, self.commit = inner, commit

    def __getattr__(self, name: str) -> Any:
        return getattr(self.inner, name)

    def pull(self, space: str, plugin: str, since: int, limit: int, **kw: Any) -> Any:
        if kw.get("full") and kw.get("after") and self.commit:
            self.commit, commit = None, self.commit
            commit()
        return self.inner.pull(space, plugin, since, limit, **kw)


def test_a_changeset_landing_during_the_first_pull_keeps_its_docs(pc: PC, ben: Ben,
                                                                  monkeypatch: pytest.MonkeyPatch) -> None:
    """The sandbox's first pull: a doc committed while the full listing paged past its key
    was never listed, yet the head moved past it, so it never came."""
    monkeypatch.setattr(vcs_engine, "PULL_LIMIT", 1)  # one item a listing page
    _v1_doc(pc, "card.a", {"v": 1}, rev=1, dirty=False)  # a copy from the older sync: the move lists everything
    ben.put_doc("card.a", {"v": 1})
    ben.put_doc("card.b", {"v": 2})
    late = _CommitsMidListing(pc.backend, lambda: ben.put_doc("card.0", {"probe": True}))  # sorts before card.a
    assert pc.run(backend=late)["state"] == "ok"
    assert ben.head() == 3 and pc.data.get("card.b") == {"v": 2}
    assert pc.run()["state"] == "ok"
    assert pc.data.get("card.0") == {"probe": True}  # the head never went past it unpulled
    assert space_vcs.head_get(pc.account, SPACE, PLUGIN)["seq"] == 3


def test_undo_never_deletes_an_item_whose_earlier_version_is_no_longer_kept(pc: PC, ben: Ben,
                                                                           world: conf.FakeWorld) -> None:
    pc.data.put("card.a", {"v": 1})
    pc.run()
    pc.data.put("card.b", {"v": 1})
    pc.run()
    pc.data.put("card.a", {"v": 3})
    pc.data.put("card.c", {"v": 1})
    pc.run()
    world.trim(SPACE, PLUGIN, 3)  # card.a's version before #3 is no longer kept
    out = vcs_engine.undo(pc.account, SPACE, PLUGIN, pc.backend, 3)
    assert out["ok"] and out["skipped"] == [["doc", "card.a"]]
    assert pc.data.get("card.a") == {"v": 3} and ben.doc("card.a") == {"v": 3}
    assert pc.data.get("card.c") is None and ben.doc("card.c") is None
    assert pc.data.get("card.b") == {"v": 1}


def test_nothing_moves_in_a_reset_space_and_upload_my_copy_sends_mine(pc: PC, ben: Ben,
                                                                     world: conf.FakeWorld) -> None:
    from backend.uefn_plugins import spaces

    pc.data.put("card.pip", {"hp": 1})
    pc.data.put("card.zed", {"hp": 1})
    pc.run()
    vcs_engine.refresh(pc.account, SPACE, [PLUGIN], pc.backend)  # this PC knows the epoch
    ben.put_doc("card.pip", {"hp": 3})
    pc.data.put("card.pip", {"hp": 2})
    assert pc.run()["conflicts"] == 1  # mine only in the conflict now
    world.server.reset_epoch(SPACE)
    assert vcs_engine.refresh(pc.account, SPACE, [PLUGIN], pc.backend)["state"] == "reset"
    assert space_vcs.conflict_get(pc.account, SPACE, PLUGIN, "doc", "card.pip") is not None  # kept for the choice
    ben._push(PLUGIN, conf.put_item("doc", "card.new", b'{"v":1}'))
    out = pc.run()
    assert out["state"] == "reset" and pc.data.get("card.new") is None  # no pull before the person chose
    got = vcs_engine.acquire(pc.account, SPACE, PLUGIN, pc.backend, [("doc:card.new", "card.new")])
    assert not got["doc:card.new"]["ok"] and pc.data.get("card.new") is None
    assert vcs_engine.restore(pc.account, SPACE, PLUGIN, pc.backend, to_seq=0)["ok"] is False
    assert spaces.settle_reset(SPACE, "upload")["ok"]
    head = space_vcs.head_get(pc.account, SPACE, PLUGIN)
    assert (head["seq"], head["remote_seq"]) == (0, 0)
    assert space_vcs.conflicts(pc.account, SPACE) == []
    assert space_vcs.unit_seqs(pc.account, SPACE, PLUGIN, ["doc:card.zed"]) == {"doc:card.zed": 0}
    assert pc.data.get("card.pip") == {"hp": 2}  # mine is what goes up
    out = pc.run()
    assert out["state"] == "ok" and pc.data.get("card.new") == {"v": 1}
    assert ben.doc("card.pip") == {"hp": 2} and ben.doc("card.zed") == {"hp": 1}


def test_unsent_conflicts_count_and_are_saved_and_a_later_loss_asks_again(pc: PC, ben: Ben) -> None:
    pc.data.put("card.pip", {"hp": 1})
    pc.run()
    ben.put_doc("card.pip", {"hp": 3})
    pc.data.put("card.pip", {"hp": 2})
    pc.run()  # mine is kept only in the conflict
    assert space_vcs.pending_unsent(pc.account, SPACE) == 1
    out = vcs_engine.save_unsent_to_local(pc.account, SPACE)
    assert [(s["key"], s["localKey"]) for s in out["saved"]] == [("card.pip", "card.pip")]
    assert pc.data.personal().get("card.pip") == {"hp": 2}
    assert space_vcs.pending_unsent(pc.account, SPACE) == 0
    time.sleep(0.01)
    pc.data.put("card.zed", {"hp": 1})  # access came back, then a new edit
    assert space_vcs.pending_unsent(pc.account, SPACE) == 1  # made after the choice: asked again
    repo.sync_put(pc.account, SPACE, state="ok")
    assert space_vcs.lost_choice_get(pc.account, SPACE) == ""


def test_drop_my_change_brings_the_teams_version_back(pc: PC, ben: Ben) -> None:
    pc.data.put("card.pip", {"hp": 1})
    pc.run()
    pc.data.put("card.pip", {"hp": 2})
    repo.set_state(pc.account, SPACE, PLUGIN, "doc", "card.pip", "refused", "Too big")
    row = repo.get(pc.account, SPACE, PLUGIN, "doc", "card.pip")
    assert repo.drop_held(pc.account, SPACE, PLUGIN, "doc", "card.pip", expect=row)
    assert pc.data.get("card.pip") is None
    assert pc.run()["state"] == "ok"
    assert pc.data.get("card.pip") == {"hp": 1}
    r = pc.row("card.pip")
    assert (r["dirty"], r["rev"]) == (0, 1) and space_vcs.blocked(pc.account, SPACE) == []


def test_a_resync_keeps_a_row_written_meanwhile_and_never_marks_a_set_aside_unit(pc: PC, ben: Ben,
                                                                                  world: conf.FakeWorld) -> None:
    pc.data.put("card.extra", {"v": 1})
    pc.data.put("card.x", {"v": 1})
    pc.run()
    world.purge(SPACE, PLUGIN, "doc", "card.extra")
    ben.put_doc("card.y", {"v": 1})
    ben.put_doc("card.z", {"v": 1})
    world.trim(SPACE, PLUGIN, 3)
    world.server.bad.add((SPACE, PLUGIN, "doc", "card.y", 2))

    def edit_during_the_download(*_: Any) -> None:
        world.server.hooks.pop("fetch", None)
        worker = threading.Thread(target=lambda: PluginData(PLUGIN).put("card.extra", {"v": 2}))
        worker.start()
        worker.join(30)

    world.server.hooks["fetch"] = edit_during_the_download
    out = pc.run(push=False)
    assert out["state"] == "ok" and pc.data.get("card.z") == {"v": 1}
    r = pc.row("card.extra")
    assert r is not None and r["dirty"] == 1 and pc.data.get("card.extra") == {"v": 2}
    assert [b["key"] for b in space_vcs.blocked(pc.account, SPACE, PLUGIN)] == ["card.y"]
    assert space_vcs.unit_seqs(pc.account, SPACE, PLUGIN, ["doc:card.y", "doc:card.z"]) == {
        "doc:card.y": 0, "doc:card.z": 3}


def test_a_doc_merges_only_against_a_base_it_knows(pc: PC, ben: Ben, world: conf.FakeWorld) -> None:
    from backend.store import db

    def forget_base(key: str) -> None:
        conn = db.connect()
        with db.write_txn(conn):
            space_vcs.base_delete(conn, pc.account, SPACE, PLUGIN, key)

    pc.data.put("card.pip", {"a": 1, "b": 1})
    pc.data.put("card.zed", {"a": 1, "b": 1})
    pc.run()
    forget_base("card.pip")
    ben.put_doc("card.pip", {"a": 1})  # Ben removed b
    pc.data.put("card.pip", {"a": 2, "b": 1})
    assert pc.run()["conflicts"] == 0  # the base was fetched: the removal holds
    assert pc.data.get("card.pip") == {"a": 2} and ben.doc("card.pip") == {"a": 2}
    forget_base("card.zed")
    ben.put_doc("card.zed", {"a": 1})
    world.trim(SPACE, PLUGIN, ben.head())  # the version mine is an edit of is no longer kept
    pc.data.put("card.zed", {"a": 2, "b": 1})
    assert pc.run()["conflicts"] == 1  # no merge without a base: b never comes back
    assert pc.data.get("card.zed") == {"a": 1} and ben.doc("card.zed") == {"a": 1}
    c = space_vcs.conflict_get(pc.account, SPACE, PLUGIN, "doc", "card.zed")
    assert _mine(c, pc.account) == {"a": 2, "b": 1}


def test_a_file_rewritten_while_it_is_sent_goes_again_with_its_new_content(pc: PC, ben: Ben,
                                                                          world: conf.FakeWorld) -> None:
    pc.run()
    pc.data.put_file("art/a.png", b"v1")

    def rewrite_before_the_upload(*_: Any) -> None:
        world.server.hooks.pop("push", None)
        worker = threading.Thread(target=lambda: PluginData(PLUGIN).put_file("art/a.png", b"v2, longer"))
        worker.start()
        worker.join(30)

    world.server.hooks["push"] = rewrite_before_the_upload
    out = pc.run()
    assert (out["state"], out["pushed"]) == ("ok", 1)
    item = ben.server.space(SPACE).repo(PLUGIN).items[("asset", "art/a.png")]
    assert item.content == b"v2, longer" and item.sha256 == hashlib.sha256(b"v2, longer").hexdigest()
    r = pc.row("art/a.png", "asset")
    assert (r["dirty"], r["attempts"]) == (0, 0)


def test_a_row_another_call_rewrites_goes_with_that_call(pc: PC, ben: Ben) -> None:
    pc.run()  # on the new sync: writes group from now on
    pc.data.put("card.a", {"v": 1})
    pc.data.put("card.b", {"v": 1})
    assert vcs_engine._build(vcs_engine.Ctx(pc.account, SPACE, PLUGIN, pc.backend), now=time.time()) == 1
    with scopes.write_group(PLUGIN, actor="agent", ducky="Builder", message="Tool tune_cards"):
        PluginData(PLUGIN).put("card.a", {"v": 2})
        assert pc.run()["pushed"] == 1  # the changeset built before goes without card.a
        assert ben.doc("card.b") == {"v": 1} and ben.doc("card.a") is None
    assert pc.run()["pushed"] == 1
    assert ben.doc("card.a") == {"v": 2}
    assert ben.client.history(SPACE, PLUGIN).entries[0].message == "Tool tune_cards"


def test_ungrouped_rows_over_one_changeset_never_hold_a_teammates_pull(pc: PC, ben: Ben, world: conf.FakeWorld,
                                                                        monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(vcs_engine, "MAX_ITEMS", 5)
    world.set_max_file_bytes(10)
    for n in range(7):
        pc.data.put(f"card.{n}", {"n": n})
    pc.data.put_file("big.bin", b"x" * 64)  # in the last slice, refused
    out = pc.run()
    assert out["pushed"] == 2 and pc.row("big.bin", "asset")["sync_state"] == "refused"
    page = ben.client.pull(SPACE, PLUGIN, 0, 50)
    assert [(cs.group, cs.last) for cs in page.changesets] == [("", True), ("", True)]
    assert not vcs_engine._groups_open(list(page.changesets), time.time())
    assert all(ben.doc(f"card.{n}") == {"n": n} for n in range(7))


class _V2Off:
    """The new sync switched off on the server: heads says ``v2`` false."""

    def __init__(self, inner: Any) -> None:
        self.inner = inner

    def __getattr__(self, name: str) -> Any:
        return getattr(self.inner, name)

    def heads(self, space: str, plugins: list[str]) -> Any:
        from dataclasses import replace

        return replace(self.inner.heads(space, plugins), v2=False)


def test_an_older_version_of_my_own_edit_coming_back_never_undoes_the_newer_one(pc: PC, ben: Ben) -> None:
    """In a live group the leader sends this PC's edits for it: one it sent earlier comes down
    while a newer one waits here. The base moves on under it, with no conflict and no flicker."""
    import collections

    from backend.uefn_plugins import live

    pc.run()  # on the new sync
    pc.data.put("card.pip", {"hp": 1})
    sent = pc.row("card.pip")["sha256"]
    live._sent_shas[(pc.account, SPACE, PLUGIN, "doc", "card.pip")] = collections.deque([sent])
    pc.data.put("card.pip", {"hp": 2})  # newer, still here
    try:
        seq = ben.put_doc("card.pip", {"hp": 1})  # the leader's commit of the older one
        out = pc.run(push=False)
        assert (out["state"], out["conflicts"]) == ("ok", 0)
        r = pc.row("card.pip")
        assert (r["dirty"], r["rev"]) == (1, seq) and pc.data.get("card.pip") == {"hp": 2}
        assert pc.run()["pushed"] == 1 and ben.doc("card.pip") == {"hp": 2}  # a plain edit on top of it
    finally:
        live.reset_for_tests()


def test_the_new_sync_switched_off_pauses_its_plugins_only(pc: PC, ben: Ben) -> None:
    pc.data.put("card.pip", {"hp": 1})
    pc.run()
    out = vcs_engine.refresh(pc.account, SPACE, [PLUGIN], _V2Off(pc.backend))
    assert out == {"state": "paused", "error": vcs_engine.SYNC_PAUSED}
    assert repo.sync_get(pc.account, SPACE)["state"] != "paused"  # the space and its older-sync plugins go on
    assert space_vcs.head_get(pc.account, SPACE, PLUGIN)["state"] == "paused"
    with pytest.raises(scopes.ReadOnlyScope, match="Team sync paused"):
        pc.data.put("card.pip", {"hp": 2})
    assert vcs_engine.refresh(pc.account, SPACE, [PLUGIN], pc.backend)["state"] == "ok"
    assert pc.data.put("card.pip", {"hp": 2})["changed"]


def test_the_drawer_shows_the_new_syncs_tables_only_on_it(pc: PC) -> None:
    from backend.uefn_plugins import spaces

    pc.data.put("card.pip", {"hp": 1})
    old = spaces.team_fields(pc.account, SPACE, PLUGIN)
    assert old["engine"] == "v1" and not {"mode", "head", "people", "locks", "nextTry"} & set(old)
    assert [o["key"] for o in old["changes"]["outgoing"]] == ["card.pip"]
    pc.run()
    new = spaces.team_fields(pc.account, SPACE, PLUGIN)
    assert new["engine"] == "v2" and {"mode", "head", "people", "locks"} <= set(new)
    assert new["changes"]["incoming"] == 0 and new["changes"]["blocked"] == []


def test_beats_go_at_most_every_ten_seconds_and_renewals_in_one_call(pc: PC, ben: Ben,
                                                                     world: conf.FakeWorld) -> None:
    now = time.time()
    assert vcs_engine.beat(pc.account, SPACE, pc.backend, [PLUGIN], now=now)["ok"]
    space_vcs.activity_note(pc.account, SPACE, PLUGIN, "doc:card.pip", at=now + 1)
    assert vcs_engine.beat(pc.account, SPACE, pc.backend, [PLUGIN], now=now + 5)["skipped"]  # changed, too soon
    world.server.offline = True
    assert not vcs_engine.beat(pc.account, SPACE, pc.backend, [PLUGIN], now=now + 11)["ok"]
    world.server.offline = False
    assert vcs_engine.beat(pc.account, SPACE, pc.backend, [PLUGIN], now=now + 15)["skipped"]  # a failed one counts
    assert vcs_engine.beat(pc.account, SPACE, pc.backend, [PLUGIN], now=now + 22)["ok"]
    units = [(f"doc:card.{n}", f"card.{n}") for n in range(4)]
    vcs_engine.acquire(pc.account, SPACE, PLUGIN, pc.backend, units[:2], reason="auto")
    vcs_engine.acquire(pc.account, SPACE, PLUGIN, pc.backend, units[2:])
    world.server.calls.clear()
    got = vcs_engine.renew(pc.account, SPACE, PLUGIN, pc.backend, [u for u, _ in units])
    assert all(v["ok"] for v in got.values()) and [c[0] for c in world.server.calls] == ["lock"]
    reasons = {lk["unit"]: lk["reason"] for lk in space_vcs.locks(pc.account, SPACE, PLUGIN)}
    assert reasons == {"doc:card.0": "auto", "doc:card.1": "auto", "doc:card.2": "manual", "doc:card.3": "manual"}


def test_a_lock_of_mine_that_went_is_told(pc: PC, ben: Ben, world: conf.FakeWorld,
                                          monkeypatch: pytest.MonkeyPatch) -> None:
    from frontend import notifications
    from frontend.ui_web import agent_modes

    alerts: list[tuple[Any, ...]] = []
    events: list[dict[str, Any]] = []
    monkeypatch.setattr(notifications, "alert", lambda *a, **k: alerts.append((*a, k.get("detail"))))
    monkeypatch.setattr(agent_modes, "push_ui_event", events.append)
    pc.data.put("card.pip", {"hp": 1})
    pc.run()
    vcs_engine.acquire(pc.account, SPACE, PLUGIN, pc.backend, [("doc:card.pip", "card.pip"), ("doc:card.zed", "Zed")])
    boss = world.client("u_olga", "d_olga", name="Olga", admin=True)
    boss.break_lock(SPACE, PLUGIN, "doc:card.zed")
    ben.client.lock(SPACE, PLUGIN, [LockUnit("doc:card.zed", "Zed")], "best", {})
    vcs_engine.refresh(pc.account, SPACE, [PLUGIN], pc.backend)
    assert [a[1] for a in alerts] == ["Your lock was broken."] and alerts[0][-1] == "Ben took Zed."
    lost = [e for e in events if e["type"] == "plugin_lock_lost"]
    assert [(e["unit"], e["holder"], e["kept"]) for e in lost] == [("doc:card.zed", "Ben", False)]


def test_workflow_saves_take_the_writers_checks_and_go_as_one_changeset(pc: PC, ben: Ben) -> None:
    from backend.automations import owned

    pc.run(WF)  # on the new sync
    scope = scopes.shared_scope(pc.account, SPACE)
    wf = {"id": "wf1", "name": "Daily check", "graph": {"nodes": []}, "updated": 1.0}
    ben.client.lock(SPACE, WF, [LockUnit("doc:wf1", "Daily check")], "best", {})
    vcs_engine.refresh(pc.account, SPACE, [WF], pc.backend)
    with pytest.raises(scopes.LockedOut, match="locked by Ben"):
        owned.write(scope, wf)
    assert pc.row("wf1", plugin=WF) is None
    ben.client.unlock(SPACE, WF, ["doc:wf1"])
    vcs_engine.refresh(pc.account, SPACE, [WF], pc.backend)
    owned.write(scope, wf)
    r = pc.row("wf1", plugin=WF)
    assert r["dirty"] == 1 and r["group_id"]
    assert [a["unit"] for a in space_vcs.activity(pc.account, SPACE)] == ["doc:wf1"]
    assert pc.run(WF)["pushed"] == 1
    assert ben.client.history(SPACE, WF).entries[0].message == "Workflow Daily check"


def test_a_teammate_the_server_has_no_name_for_reads_as_a_teammate_never_as_a_user_id(
        pc: PC, ben: Ben, world: conf.FakeWorld, monkeypatch: pytest.MonkeyPatch) -> None:
    from backend.uefn_plugins import spaces
    from frontend.ui_web import agent_modes

    uid = "01a14b07-3d9c-78d2-88f6-684331c12a1a"
    sam = world.client(uid, "d_sam")  # no display name: the server only has the user id
    events: list[dict[str, Any]] = []
    monkeypatch.setattr(agent_modes, "push_ui_event", events.append)
    pc.data.put("card.pip", {"hp": 1, "name": "Pip"})
    pc.data.put("card.zed", {"hp": 1})
    pc.run()
    sam.push(SPACE, PLUGIN, conf.changeset(
        conf.put_item("doc", "card.pip", scopes.encode_doc({"hp": 1, "name": "Pipo"}), base=1),
        conf.put_item("doc", "card.zed", scopes.encode_doc({"hp": 3}), base=1)))
    pc.data.put("card.pip", {"hp": 2, "name": "Pip"})  # merges
    pc.data.put("card.zed", {"hp": 2})  # clashes
    assert pc.run()["conflicts"] == 1
    assert ben.client.history(SPACE, PLUGIN).entries[0].message == "Merged with a change by a teammate"
    assert pc.row("card.zed")["sync_error"] == "A teammate changed this too."
    assert [c["by"] for c in vcs_engine.status(pc.account, SPACE, PLUGIN)["conflicts"]] == ["a teammate"]

    sam.lock(SPACE, PLUGIN, [LockUnit("doc:card.lock", "card.lock")], "best", {})
    sam.beat(SPACE, {"label": "Sam's PC"})
    vcs_engine.refresh(pc.account, SPACE, [PLUGIN], pc.backend)
    with pytest.raises(scopes.LockedOut, match="card.lock is locked by a teammate"):
        pc.data.put("card.lock", {"v": 1})
    got = vcs_engine.acquire(pc.account, SPACE, PLUGIN, pc.backend, [("doc:card.lock", "card.lock")])
    assert (got["doc:card.lock"]["holder"], got["doc:card.lock"]["error"]) == ("a teammate", "Locked by a teammate")
    fields = spaces.team_fields(pc.account, SPACE, PLUGIN)
    assert [c["by"] for c in fields["conflicts"]] == ["a teammate"]
    assert [lk["holder"] for lk in fields["locks"]] == ["a teammate"]
    assert [(p["name"], p["user"]) for p in fields["people"]] == [("A teammate", uid)]  # the id only as an id
    shown = {**fields, "people": [{k: v for k, v in p.items() if k != "user"} for p in fields["people"]]}
    assert uid not in json.dumps([shown, events, vcs_engine.status(pc.account, SPACE, PLUGIN)["locks"]])


def test_my_other_pc_holds_it_so_this_pc_is_locked_out_and_says_which_pc(
        pc: PC, world: conf.FakeWorld, monkeypatch: pytest.MonkeyPatch) -> None:
    """One account on two PCs: a lock is one PC's, so this PC is locked out, and every
    message says it's your other PC by its name (not "locked by Ana", your own name)."""
    from backend.uefn_plugins import spaces
    from frontend.ui_web import agent_modes

    monkeypatch.setattr(vcs_engine, "my_user_id", lambda: "u_ana")
    monkeypatch.setattr(agent_modes, "push_ui_event", lambda _e: None)
    pc.run()  # on the new sync
    main = world.server.client("u_ana", "d_mainpc", name="Ana")  # the same account on another PC
    main.lock(SPACE, PLUGIN, [LockUnit("doc:card.lock", "Crate")], "best", {})
    main.beat(SPACE, {"label": "MainPC"})
    vcs_engine.refresh(pc.account, SPACE, [PLUGIN], pc.backend)
    with pytest.raises(scopes.LockedOut, match="Crate is open on your other PC, MainPC.*locked on this PC") as got:
        pc.data.put("card.lock", {"v": 1})
    assert (got.value.lock["holder"], got.value.lock["elsewhere"]) == ("you on MainPC", "MainPC")
    assert pc.row("card.lock") is None
    fields = spaces.team_fields(pc.account, SPACE, PLUGIN)
    assert [(lk["holder"], lk["elsewhere"]) for lk in fields["locks"]] == [("you on MainPC", "MainPC")]
    assert [(p["self"], p["me"]) for p in fields["people"] if p["device"] == "MainPC"] == [(True, False)]
    ans = vcs_engine.acquire(pc.account, SPACE, PLUGIN, pc.backend, [("doc:card.lock", "Crate")])["doc:card.lock"]
    assert (ans["state"], ans["elsewhere"], ans["error"]) == ("locked", "MainPC",
                                                             "It's open on your other PC, MainPC: locked there")


def test_a_teammates_ai_change_says_ai_in_conflicts_and_update_now(pc: PC, ben: Ben) -> None:
    from dataclasses import replace

    pc.data.put("card.zed", {"hp": 1})
    pc.run()
    cs = conf.changeset(conf.put_item("doc", "card.zed", scopes.encode_doc({"hp": 3}), base=1))
    assert isinstance(ben.client.push(SPACE, PLUGIN, replace(cs, actor="agent", ducky="Builder")), Committed)
    pc.data.put("card.zed", {"hp": 2})  # clashes with Ben's ducky's change
    assert pc.run()["conflicts"] == 1
    assert [c["by"] for c in vcs_engine.status(pc.account, SPACE, PLUGIN)["conflicts"]] == ["AI (Ben's ducky Builder)"]


def test_an_ai_write_outside_a_write_group_goes_out_as_the_ais_not_the_persons(pc: PC, ben: Ben) -> None:
    """Under the person's own account: a ducky's change is the ducky's in the team history."""
    from backend.workspace import identity

    pc.run()  # on the new sync
    token = identity.bind(identity.RunContext(run_id="r1", conv_id="c1", ducky_name="Builder"))
    try:
        assert scopes.caller() == ("agent", "Builder")
        pc.data.put("card.ai", {"hp": 9})
    finally:
        identity.reset(token)
    pc.data.put("card.me", {"hp": 1})  # the person, by hand
    assert pc.run()["pushed"] == 2
    by = sorted((e.actor, e.ducky) for e in ben.client.history(SPACE, PLUGIN).entries)
    assert by == [("agent", "Builder"), ("user", "")]


def test_a_write_in_the_mcp_bridge_is_an_ais_even_with_no_duckys_name(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("UEFN_DUCKY_MCP_BRIDGE", "1")
    assert scopes.caller() == ("agent", "")
    monkeypatch.delenv("UEFN_DUCKY_MCP_BRIDGE")
    assert scopes.caller() == ("user", "")


def test_author_names_ais_and_my_other_pc(pc: PC, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(vcs_engine, "my_user_id", lambda: "u_ana")
    monkeypatch.setattr(vcs_engine, "device_names", lambda _a, _s: {"d_mainpc": "MainPC"})
    a = (pc.account, SPACE)
    assert vcs_engine.author(*a, "Ben") == "Ben"
    assert vcs_engine.author(*a, "Ben", "d_ben", "agent", "Builder") == "AI (Ben's ducky Builder)"
    assert vcs_engine.author(*a, "Ben", "d_ben", "agent") == "AI, for Ben"
    assert vcs_engine.author(*a, "u_ana", "d_mainpc") == "you on MainPC"
    assert vcs_engine.author(*a, "u_ana", "d_mainpc", "agent", "Builder") == "AI (your ducky Builder on MainPC)"
    assert vcs_engine.other_pc(*a, "u_ana", vcs_engine.device_id()) == ""  # this PC is not "another"


def test_a_duckys_lock_names_the_ducky_not_only_its_person(pc: PC, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(vcs_engine, "my_user_id", lambda: "u_ana")
    roster = {"people": [{"user": "u_ben", "device": "d_ben", "plugins": {PLUGIN: {"editing": [
        {"unitName": "Pip", "actor": "agent", "ducky": "Builder"}]}}}]}
    monkeypatch.setattr(space_vcs, "presence_get", lambda _a, _s: {"roster": roster})
    monkeypatch.setattr(space_vcs, "activity", lambda _a, _s, **_k: [
        {"plugin_id": PLUGIN, "unit": "doc:card.zed", "actor": "agent", "ducky": "Verse Coder"}])
    a = (pc.account, SPACE)
    ben = {"plugin_id": PLUGIN, "unit": "doc:card.pip", "name": "Pip", "holder_user": "u_ben",
           "holder_name": "Ben", "holder_device": "d_ben", "mine": False}
    assert vcs_engine.holder_of(*a, ben)[0] == "AI (Ben's ducky Builder)"
    assert vcs_engine.holder_of(*a, {**ben, "name": "Zed", "unit": "doc:card.zed"})[0] == "Ben"  # Ben, by hand
    mine = {"plugin_id": PLUGIN, "unit": "doc:card.zed", "name": "Zed", "mine": True}
    assert vcs_engine.holder_of(*a, mine)[0] == "AI (your ducky Verse Coder)"
    assert vcs_engine.lock_ducky(*a, {**mine, "unit": "doc:card.other"}) is None
