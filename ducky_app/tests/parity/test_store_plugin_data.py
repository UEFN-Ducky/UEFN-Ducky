"""Plugin docs and files, the spaces' sync rows and the plugins' links."""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from backend.store.repos import plugin_data

from parity_harness import callers, plain
from test_store_chats import _sql

_TABLES = {
    "plugin_data": "account_id, scope_id, plugin_id, kind, key, value, size, sha256, rev, dirty, deleted, sensitive, updated, group_id, "
                   "sync_state, sync_error, attempts, next_try>0",
    "scope_sync": "account_id, space_id, label, members, cursor_rev, state, error, usage, synced_at, called_at, rewind_rev, rewind_at>0",
    "plugin_scopes": "account_id, plugin_id, scope_id",
    "space_incoming_blocked": "account_id, space_id, plugin_id, kind, key, unit, seq, error, attempts, next_try",
    "space_heads": "account_id, space_id, plugin_id, seq, remote_seq, engine, moves, attempts, next_try",
    "space_lost_choice": "account_id, space_id, choice",
}
A = {"account": "a", "scope": "s1", "plugin": "p1"}


def _stored() -> dict:
    return {table: sorted(_sql(f"SELECT {cols} FROM {table}"), key=repr) for table, cols in _TABLES.items()}


def _python(function: str, **args):
    if function == "sync_put":
        return plugin_data.sync_put(args["account"], args["space"], **args["fields"])
    if function == "note_attempts":
        args["items"] = [tuple(item) for item in args["items"]]
    return getattr(plugin_data, function)(**args)


def _doc(key: str, **more) -> dict:
    return {**A, "kind": "doc", "key": key, **more}


def _wait(noted: dict | None) -> dict | None:
    """A noted attempt with its wait in half minutes instead of the time it ends."""
    return noted and {**noted, "next_try": round((noted["next_try"] - time.time()) / 30)}


def _timeless(value):
    """Rows as read, with whether each waits instead of until when."""
    if isinstance(value, dict):
        return {key: bool(item) if key == "next_try" else _timeless(item) for key, item in value.items()}
    return [_timeless(item) for item in value] if isinstance(value, list) else value


def _script(call, tmp: Path) -> list:
    """The life of one space's copy through either backend: every answer, and the tables
    at each stage."""
    stamps = iter(range(1, 10_000))
    out: list = []

    def w(function: str, **args):
        answer = call(function, **args)
        # Rows written by the call get a stamp that is the same on both runs.
        _sql("UPDATE plugin_data SET updated=? WHERE updated>1000000000", next(stamps))
        return answer

    def file(name: str) -> tuple[Path, Path]:
        temp = tmp / "copy" / "deep" / f"{name}.tmp"
        temp.parent.mkdir(parents=True, exist_ok=True)
        temp.write_bytes(b"sealed " + name.encode())
        return temp, tmp / "copy" / "deep" / "er" / name

    # Writes: a local write keeps the stored rev, a write starts the row's sync afresh.
    w("put", **_doc("k1"), value="enc1:one", size=3, sha256="h1", dirty=True)
    w("put", **_doc("k2"), value="enc1:two é", size=5, sha256="h2", dirty=True, rev=4, sensitive=False, group_id="g1")
    w("put", **_doc("k_3"), value=None, size=0, sha256="", dirty=False, rev=2)
    w("put", **_doc("kx3"), value="v", size=1, sha256="h3", dirty=True, sensitive=True)
    w("put", **_doc("k1"), value="enc1:one again", size=9, sha256="h1b", dirty=True)
    w("put", **_doc("k2"), value="v2", size=2, sha256="h2b", dirty=False, rev=None)
    w("put", account="a", scope="personal", plugin="p2", kind="doc", key="k1", value="local", size=5, sha256="l1", dirty=True)
    w("put", account="b", scope="s1", plugin="p1", kind="doc", key="k1", value="other account", size=1, sha256="o", dirty=True)
    temp, target = file("a.bin")
    w("put_file_row", **A, key="files/a.bin", temp_path=str(temp), target_path=str(target), size=6, sha256="f1", dirty=True, group_id="g2")
    out += [[temp.exists(), target.read_bytes().decode()], _stored()]

    def reads() -> list:
        return [
            [call("get", **_doc(key)) for key in ("k1", "k2", "none")],
            [call("rows", **A, kind=kind, prefix=prefix) for kind, prefix in (("doc", "k_"), ("doc", "k"), ("asset", ""), ("doc", "none"))],
            call("rows", **A, kind="doc", prefix="", values=False),
            [call("written_since", **A, since=since) for since in (0, 4, 99)],
            [call("plugin_ids", account=account, scope=scope) for account, scope in (("a", "s1"), ("a", "personal"), ("b", "s1"), ("a", "none"))],
            [call("scope_plugin_ids", account="a", scope=scope) for scope in ("s1", "none")],
            [call("scope_ids", account=account) for account in ("a", "b", "none")],
            [call("has_rows", account="a", plugin=plugin, scope=scope) for plugin in ("p1", "p2", "none") for scope in (None, "s1", "personal")],
            [call("dirty", account="a", scope="s1", limit=limit) for limit in (10, 1)],
            [call("count_dirty", account="a", scope="s1", plugin=plugin) for plugin in (None, "p1", "none")],
            [call("unsent_spaces", account=account) for account in ("a", "b", "none")],
            [call("totals", account="a", scope=scope, plugin="p1") for scope in ("s1", "none")],
            [call("pending_items", account="a", scope="s1", plugin="p1", limit=limit) for limit in (200, 1)],
            [call("state_counts", account="a", scope="s1", plugin=plugin) for plugin in (None, "p1", "none")],
            [call("state_items", account="a", scope="s1", plugin=plugin, limit=limit) for plugin, limit in ((None, 50), ("p1", 1), ("none", 50))],
        ]

    out.append(reads())

    # Why a row isn't going out.
    states = [
        w("set_state", **_doc("k1"), state="refused", reason="x" * 600),
        w("set_state", **_doc("k2"), state="conflict", reason="both edited é", expect_sha="h2b"),
        w("set_state", **_doc("k2"), state="blocked", expect_sha="nope"),
        w("set_state", **_doc("k2"), state="blocked", expect_sha=""),
        w("set_state", **_doc("none"), state="refused"),
    ]
    assert states == [True, True, False, False, False]
    w("clear_dirty", **_doc("k_3"))
    out += [reads(), _stored()]
    assert w("requeue_refused", account="a", scope="s1") == 2
    w("set_state", **_doc("k2"), state="")
    noted = [_wait(w("note_attempt", **_doc("k1"), error="disk é " + "y" * 600)) for _ in range(5)]
    assert [(n["attempts"], n["next_try"], n["state"]) for n in noted] == [(1, 2, ""), (2, 4, ""), (3, 8, ""), (4, 16, ""), (5, 32, "blocked")]
    assert w("note_attempt", **_doc("none"), error="x") is None
    together = _wait(w("note_attempts", **A, items=[["doc", "k2"], ["doc", "k_3"], ["doc", "k2"], ["doc", "none"]], error="server said no"))
    assert together == {"attempts": 1, "next_try": 2, "state": ""} and w("note_attempts", **A, items=[["doc", "none"]], error="x") is None
    out += [reads(), _stored()]

    # A push the server confirmed, deletes, and queueing again.
    w("mark_pushed", **_doc("k1"), rev=7, sha256="h1b")  # still that content: clean
    w("mark_dirty", **_doc("k2"))
    w("mark_pushed", **_doc("k2"), rev=8, sha256="stale")  # edited meanwhile: stays queued
    w("remove", **_doc("k_3"), tombstone=True, group_id="g3")
    out.append(call("get", **_doc("k_3")))
    w("mark_pushed", **_doc("k_3"), rev=9, sha256="")  # the tombstone goes
    w("remove", **_doc("kx3"), tombstone=True)
    w("put", **_doc("kx3"), value="back", size=4, sha256="h9", dirty=True)
    w("mark_pushed", **_doc("kx3"), rev=10, sha256="whatever", pushed_delete=True)  # made again since: kept
    w("set_rev", **_doc("k1"), rev=11)
    w("mark_dirty", **_doc("none"))
    out.append(_stored())
    w("mark_plugin_dirty", **A)
    w("remove", **A, kind="asset", key="files/a.bin", tombstone=False, target_path=str(target))
    w("remove", **A, kind="asset", key="files/none.bin", tombstone=False, target_path=str(target))
    out += [target.exists(), reads(), _stored()]

    # Pulled copies land only where no local write won the race.
    landed = [w("apply_remote", **_doc("r1"), value="theirs", size=6, sha256="t1", rev=20, expect=None)]
    got = call("get", **_doc("r1"))
    landed.append(w("apply_remote", **_doc("r1"), value="theirs 2", size=8, sha256="t2", rev=21, expect=got))
    landed.append(w("apply_remote", **_doc("r1"), value="x", size=1, sha256="t3", rev=22, expect=got))
    landed.append(w("apply_remote", **_doc("r1"), value="x", size=1, sha256="t3", rev=22, expect=None))
    landed.append(w("apply_remote", **_doc("k1"), value="x", size=1, sha256="t3", rev=22, expect=call("get", **_doc("k1"))))  # dirty
    asset = {**A, "kind": "asset", "key": "files/b.bin"}
    temp, target = file("b.bin")
    landed.append(w("apply_remote", **asset, value=None, size=3, sha256="fb", rev=23, expect=None, temp_path=str(temp), target_path=str(target)))
    landed.append([temp.exists(), target.read_bytes().decode()])
    temp, _ = file("c.bin")
    landed.append(w("apply_remote", **asset, value=None, size=3, sha256="fc", rev=24, expect=None, temp_path=str(temp), target_path=str(target)))
    landed.append([temp.exists(), target.read_bytes().decode()])
    got = call("get", **asset)
    landed.append(w("remove_remote", **asset, expect=None, target_path=str(target)))
    landed.append(w("remove_remote", **asset, expect={"sha256": "fb", "deleted": 1}, target_path=str(target)))
    landed.append([target.exists(), w("remove_remote", **asset, expect=got, target_path=str(target)), target.exists()])
    landed.append(w("remove_remote", **_doc("gone"), expect=None))
    assert landed == [True, True, False, False, False, True, [False, "sealed b.bin"], False, [False, "sealed b.bin"], False, False, [True, True, False], True]
    out.append(_stored())

    # The space's sync row; a dropped change comes back from the team.
    out.append(call("sync_get", account="a", space="s1"))
    w("sync_put", account="a", space="s1", fields={"label": "Team é", "members": 3, "cursor_rev": 50, "state": "ok", "usage": {"bytes": 10, "né": "é"},
                                                   "synced_at": 5, "called_at": 6.5, "bogus": 1})
    w("put", **_doc("d1"), value="mine", size=4, sha256="m1", dirty=True, rev=30)
    w("set_state", **_doc("d1"), state="refused", reason="no")
    held = call("get", **_doc("d1"))
    dropped = [w("drop_held", **_doc("d1"), expect={**held, "sha256": "other"}), w("drop_held", **_doc("d1"), expect=held), w("drop_held", **_doc("d1"), expect=held)]
    w("sync_put", account="a", space="s1", fields={"cursor_rev": 60})  # a round from before the drop: held at the dropped rev
    out += [call("sync_get", account="a", space="s1"), _stored()["scope_sync"]]
    w("sync_put", account="a", space="s1", fields={"called_at": 4_000_000_000.0})
    w("sync_put", account="a", space="s1", fields={"cursor_rev": 60, "error": None})  # a round that started after it
    out += [call("sync_get", account="a", space="s1"), call("sync_get", account="a", space="none")]
    _sql("INSERT INTO space_heads(account_id, space_id, plugin_id, engine, seq, remote_seq, moves) VALUES ('a', 's1', 'p1', 'v2', 5, 6, 1)")
    for rev in (31, 29):
        w("put", **_doc("d2"), value="mine", size=4, sha256="m2", dirty=True, rev=rev)
        w("set_state", **_doc("d2"), state="blocked", reason="kept failing")
        temp, target = file("d2.bin")
        temp.replace(target)
        dropped.append(w("drop_held", **_doc("d2"), expect=call("get", **_doc("d2")), target_path=str(target)))
        dropped.append(target.exists())
    assert dropped == [False, True, False, True, False, True, False]
    w("sync_put", account="a", space="s2", fields={"usage": None, "label": 5})
    w("sync_put", account="a", space="s2", fields={"bogus": 1})
    _sql("INSERT INTO space_lost_choice(account_id, space_id, choice) VALUES ('a', 's2', 'keep'), ('a', 's1', 'keep')")
    w("sync_put", account="a", space="s2", fields={"state": "ok"})
    out.append(_stored())
    _sql("UPDATE scope_sync SET usage='not json' WHERE space_id='s2'")
    out.append(call("sync_get", account="a", space="s2"))

    # Where each plugin's data lives.
    for plugin, scope in (("p1", "s1"), ("p2", "s2"), ("p3", "s2"), ("p2", "personal"), ("p1", "s1")):
        w("link_set", account="a", plugin=plugin, scope=scope)
    out += [[call("link_get", account="a", plugin=plugin) for plugin in ("p1", "p2", "p3")], call("links", account="a"), call("links", account="none")]

    # The space was reset on the server; plugins and spaces that go.
    w("remove", **_doc("k2"), tombstone=True)
    w("set_state", **_doc("k1"), state="refused", reason="plan")
    out.append(w("settle_reset", account="a", scope="s1", keep=True))
    out.append(_stored())
    out.append(w("settle_reset", account="a", scope="s1", keep=False))
    out.append(_stored())
    out += [w("delete_plugin", account="a", plugin="p2"), w("delete_plugin", account="a", plugin="none")]
    w("delete_scope_plugin", account="b", scope="s1", plugin="p1")
    w("delete_scope", account="a", scope="s2")
    out.append(_stored())
    return _timeless(plain(out))


def test_a_space_copy_lives_the_same_through_both(rust_store, tmp_path: Path) -> None:
    _, rust = callers(rust_store, "plugin_data", plugin_data)
    seen = []
    for name, call in (("python", _python), ("rust", rust)):
        for table in _TABLES:
            _sql(f"DELETE FROM {table}")
        seen.append(_script(call, tmp_path / name))
    for index, (theirs, ours) in enumerate(zip(*seen)):
        assert ours == theirs, f"answer {index}"
    assert len(seen[0]) == len(seen[1])
    last = seen[0][-1]
    assert last["plugin_data"] == [] and last["plugin_scopes"] == [["a", "p1", "s1"]] and [row[1] for row in last["scope_sync"]] == ["s1"]


def test_an_unknown_state_is_refused_by_both(rust_store) -> None:
    with pytest.raises(ValueError) as refused:
        plugin_data.set_state("a", "s1", "p1", "doc", "k1", "weird")
    status, reply = rust_store.request("/__rs/store/plugin_data/set_state", {**_doc("k1"), "state": "weird"})
    assert status == 200 and reply == {"ok": False, "error": str(refused.value)}
