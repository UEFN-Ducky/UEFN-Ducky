"""The duckies repo: sealed for the account, written by one backend and read by the other.

Every test runs signed out and signed in (the ``account`` fixture).
"""

from __future__ import annotations

from backend.store import db
from backend.store.repos import duckies

from .parity_harness import plain

_QUACKER = {"name": "Quacker", "prompt": "Be brief. é", "tools": ["verse", {"x": None}], "temp": 0.5}


def _raw(ducky_id: str) -> str:
    return db.connect().execute("SELECT data FROM duckies WHERE ducky_id=?", (ducky_id,)).fetchone()[0]


def _same_everywhere(rust_store, **kw) -> list:
    """Both backends list the same rows; returns them."""
    ours = rust_store.store("duckies", "list_all", **kw)
    assert ours == plain(duckies.list_all(**kw))
    return ours


def test_rust_reads_the_ducky_python_saved(rust_store, account) -> None:
    duckies.save("d1", "custom", _QUACKER)
    assert rust_store.store("duckies", "get", ducky_id="d1") == plain(duckies.get("d1"))
    rows = _same_everywhere(rust_store)
    assert [(r["id"], r["kind"], r["data"], r["archived"], r["deleted"]) for r in rows] == [
        ("d1", "custom", _QUACKER, False, 0)
    ]
    assert rust_store.store("duckies", "get", ducky_id="missing") is None


def test_python_reads_the_ducky_rust_saved_and_it_is_sealed_in_the_file(rust_store, account) -> None:
    assert rust_store.store("duckies", "save", ducky_id="d1", kind="custom", data=_QUACKER) is None
    assert _raw("d1").startswith("enc1:") and "Quacker" not in _raw("d1")
    got = duckies.get("d1")
    assert got["data"] == _QUACKER and got["created"] == got["updated"] > 0
    assert db.connect().execute("SELECT id, account_id FROM duckies").fetchall()[0][:] == (f"{account}~d1", account)
    _same_everywhere(rust_store)


def test_every_save_keeps_the_copy_it_replaces(rust_store, account) -> None:
    duckies.save("d1", "custom", {"v": 1})
    rust_store.store("duckies", "save", ducky_id="d1", kind="custom", data={"v": 2})
    duckies.save("d1", "custom", {"v": 3})
    history = rust_store.store("duckies", "history", ducky_id="d1")
    assert history == plain(duckies.history("d1"))
    assert [h["data"] for h in history] == [{"v": 2}, {"v": 1}]  # newest first
    assert duckies.get("d1")["data"] == {"v": 3}


def test_archiving_a_shipped_ducky_makes_its_row(rust_store, account) -> None:
    rust_store.store("duckies", "set_archived", ducky_id="developer", kind="bundled", archived=True)
    got = duckies.get("developer")
    assert (got["kind"], got["data"], got["archived"]) == ("bundled", {}, True)
    duckies.set_archived("developer", "bundled", False)
    assert rust_store.store("duckies", "get", ducky_id="developer")["archived"] is False
    duckies.set_archived("artist", "bundled", True)
    assert rust_store.store("duckies", "get", ducky_id="artist") == plain(duckies.get("artist"))
    # Archiving never touches the fields.
    duckies.save("d1", "custom", _QUACKER)
    rust_store.store("duckies", "set_archived", ducky_id="d1", kind="custom", archived=True)
    assert duckies.get("d1")["data"] == _QUACKER and duckies.get("d1")["archived"] is True


def test_deleting_keeps_the_row_and_hides_it(rust_store, account) -> None:
    duckies.save("d1", "custom", _QUACKER)
    assert rust_store.store("duckies", "set_deleted", ducky_id="d1", deleted=True) is True
    assert _same_everywhere(rust_store) == []
    kept = _same_everywhere(rust_store, include_deleted=True)
    assert len(kept) == 1 and kept[0]["deleted"] > 0 and kept[0]["data"] == _QUACKER
    assert duckies.set_deleted("d1", False) is True  # brought back by the other backend
    assert [r["id"] for r in _same_everywhere(rust_store)] == ["d1"]
    # A custom ducky must exist; a shipped one gets a row.
    assert rust_store.store("duckies", "set_deleted", ducky_id="nope", deleted=True) is False
    assert duckies.set_deleted("nope2", True) is False
    assert rust_store.store("duckies", "set_deleted", ducky_id="developer", deleted=True, kind="bundled") is True
    assert duckies.set_deleted("artist", True, kind="bundled") is True
    assert rust_store.store("duckies", "set_deleted", ducky_id="d1", deleted=True, kind="bundled") is True
    rows = {r["id"]: r for r in _same_everywhere(rust_store, include_deleted=True)}
    assert set(rows) == {"d1", "developer", "artist"} and all(r["deleted"] > 0 for r in rows.values())


def test_a_synced_copy_is_taken_only_when_it_is_newer_and_different(rust_store, account) -> None:
    def both(ducky: str, kind: str, data: dict, updated: float) -> bool:
        """The same call on the Rust store (ducky ``r-…``) and the Python one (``p-…``)."""
        ours = rust_store.store("duckies", "save_synced", ducky_id="r-" + ducky, kind=kind, data=data, updated=updated)
        theirs = duckies.save_synced("p-" + ducky, kind, data, updated)
        assert ours is theirs
        return ours

    assert both("d", "custom", {"b": 1, "a": 2}, 100.0) is True  # new: comes in
    assert both("d", "custom", {"a": 2, "b": 1}, 200.0) is False  # the same fields in another order
    assert both("d", "custom", {"a": 3}, 50.0) is False  # older than what is here
    assert both("d", "bundled", {"a": 3}, 300.0) is False  # another kind
    assert both("d", "custom", {"a": 3}, 300.0) is True
    for ducky in ("r-d", "p-d"):
        got = duckies.get(ducky)
        assert (got["data"], got["created"], got["updated"]) == ({"a": 3}, 100.0, 300.0)
        assert rust_store.store("duckies", "get", ducky_id=ducky) == plain(got)
        assert [h["data"] for h in duckies.history(ducky)] == [{"b": 1, "a": 2}]
    # Each backend refuses the other's copy the same way.
    assert rust_store.store("duckies", "save_synced", ducky_id="p-d", kind="custom", data={"a": 3}, updated=999.0) is False
    assert duckies.save_synced("r-d", "custom", {"a": 3}, 999.0) is False


def test_an_import_adds_only_the_duckies_that_are_missing(rust_store, account) -> None:
    duckies.save("have", "custom", {"mine": True})
    rows = [
        {"id": "have", "kind": "custom", "data": {"mine": False}, "created": 5.0},
        {"id": "new1", "kind": "custom", "data": {"n": 1}, "created": 7.0, "updated": 9.0, "archived": True},
        {"id": "new2", "kind": "bundled", "data": None, "created": 8.0},
    ]
    assert rust_store.store("duckies", "insert_missing", rows=rows) == 2
    assert duckies.insert_missing(rows) == 0
    got = {r["id"]: r for r in _same_everywhere(rust_store)}
    assert got["have"]["data"] == {"mine": True}
    assert (got["new1"]["data"], got["new1"]["archived"], got["new1"]["created"], got["new1"]["updated"]) == (
        {"n": 1}, True, 7.0, 9.0,
    )
    assert (got["new2"]["data"], got["new2"]["updated"]) == ({}, 8.0)
    # Oldest first, the order the person made them.
    assert [r["id"] for r in _same_everywhere(rust_store)][:2] == ["new1", "new2"]


def test_another_accounts_duckies_are_not_listed(rust_store, account) -> None:
    other = "_local" if account != "_local" else "a_0000000000000000"
    db.connect().execute(
        "INSERT INTO duckies(id, account_id, ducky_id, kind, data) VALUES (?, ?, 'theirs', 'custom', '{}')",
        (f"{other}~theirs", other),
    )
    duckies.save("mine", "custom", {})
    assert [r["id"] for r in _same_everywhere(rust_store, include_deleted=True)] == ["mine"]
    assert rust_store.store("duckies", "get", ducky_id="theirs") is None
