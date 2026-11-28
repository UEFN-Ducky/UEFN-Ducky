"""The ledger repo: the change journal's runs, entries, blobs, path index and watch index."""

from __future__ import annotations

import copy

import pytest

from backend.store.repos import ledger

from .parity_harness import plain
from .test_store_chats import _sql

_TABLES = {
    "runs": "*", "run_entries": "*", "run_seen": "*", "path_index": "*", "watch_index": "*",
    "blobs": "hash, text, size",  # not when it was made
}


def _snapshot(*, clear: bool = True) -> dict:
    """Every ledger row; ``clear`` empties the tables for the other backend's turn."""
    out = {table: sorted(_sql(f"SELECT {columns} FROM {table}"), key=repr) for table, columns in _TABLES.items()}
    if clear:
        for table in _TABLES:
            _sql(f"DELETE FROM {table}")
    return out


def _run(run_id: str, **fields) -> dict:
    return copy.deepcopy({
        "run_id": run_id, "conv_id": "c1", "group_id": "g1", "started": 100.5, "ended": None, "status": "running",
        "archived": False, "ducky_name": "Dév", "note": {"z": 1, "a": [1.5, None, "é"]},
        "entries": [
            {"seq": 1, "path": "a.txt", "ts": 110.0, "outcome": "ok", "before_blob": "", "after_blob": "b1", "tool": "write"},
            {"seq": 2, "path": "b/é.txt", "ts": 120, "outcome": "failed", "after_blob": None},
            {"seq": 3, "path": "a.txt", "ts": 130.25},
            {"seq": 4, "path": "d.txt", "ts": 125.0, "before_blob": "b0"},
        ],
        "seen": {"a.txt": {"hash": "h", "mtime": 1.5}, "c.txt": [1, 2]},
        **fields,
    })


def _journal(put, delete) -> list:
    """The same writes through either backend; what the deletes answered."""
    put("p1", _run("r1"))
    put("p1", _run("r2", started=50, ended=75.5, status="done", archived=1, conv_id=None, entries=[], seen={}))
    put("p2", _run("r3", status="", ducky_name=None, seen=None, started=None))
    put("p2", _run("r5", ducky_name=7, started=300.0, entries=[{"seq": 1, "path": "x.txt", "ts": 310.0}]))
    changed = _run("r1", ended=200)
    changed["entries"][0]["tool"] = "edit"
    del changed["entries"][1]
    changed["seen"] = {"z.txt": "s"}
    put("p1", changed)
    put("p1", _run("r4"))
    # Another project's delete still drops the entries, as Python's does.
    return [delete("p1", "r4"), delete("p1", "r4"), delete("p1", "r3"), delete("p9", "none")]


def _rust(rust_store, function: str, *names: str):
    return lambda *args: rust_store.store("ledger", function, **dict(zip(names, args)))


def test_runs_are_stored_and_read_the_same(rust_store) -> None:
    deleted = _journal(ledger.run_put, ledger.run_delete)
    theirs = _snapshot()
    assert _journal(_rust(rust_store, "run_put", "project_id", "run"), _rust(rust_store, "run_delete", "project_id", "run_id")) == deleted
    assert _snapshot(clear=False) == theirs and len(theirs["run_entries"]) == 4

    for project in ("p1", "p2", "none"):
        for run_id in ("r1", "r2", "r3", "r5", "none"):
            assert rust_store.store("ledger", "run_get", project_id=project, run_id=run_id) == ledger.run_get(project, run_id)
        assert rust_store.store("ledger", "runs_docs", project_id=project) == ledger.runs_docs(project)
        assert rust_store.store("ledger", "runs_brief", project_id=project) == ledger.runs_brief(project)
        assert rust_store.store("ledger", "catalog", project_id=project) == ledger.catalog(project)
        for statuses in ([], ["running"], ["done", "running", "nope"]):
            assert rust_store.store("ledger", "runs_with_status", project_id=project, statuses=statuses) == ledger.runs_with_status(
                project, tuple(statuses)
            )
        for paths, since in ((["a.txt"], 0.0), (["a.txt", "d.txt"], 126.0), (["a.txt"], 999.0), ([], 0.0), (["x.txt", "none"], 0.0)):
            assert rust_store.store("ledger", "latest_entry_for_path", project_id=project, paths=paths, since=since) == (
                ledger.latest_entry_for_path(project, paths, since=since)
            )
    for since, max_files in ((0.0, 8), (0.0, 1), (126.0, 8), (999.0, 8)):
        assert rust_store.store("ledger", "active_work", since=since, max_files=max_files) == plain(
            ledger.active_work(since, max_files=max_files)
        )
    assert [run["run_id"] for run in ledger.active_work(0.0)] == ["r5", "r1"]


def test_a_run_of_another_project_is_refused(rust_store) -> None:
    ledger.run_put("p1", _run("r1"))
    before = _snapshot(clear=False)
    with pytest.raises(ValueError, match="belongs to another project") as refused:
        ledger.run_put("p2", _run("r1", status="done"))
    status, reply = rust_store.request("/__rs/store/ledger/run_put", {"project_id": "p2", "run": _run("r1", status="done")})
    assert (status, reply) == (200, {"ok": False, "error": str(refused.value)})
    assert _snapshot(clear=False) == before


@pytest.mark.parametrize("which", [{"started_before": 150.0}, {"run_ids": ["r1", "r3", "none"]}, {"run_ids": []}, {"started_before": 0.0}])
def test_stale_runs_are_closed_the_same(rust_store, which: dict) -> None:
    def runs() -> None:
        ledger.run_put("p", _run("r1"))
        ledger.run_put("p", _run("r2", started=200.0))
        ledger.run_put("p", _run("r3", status="done"))

    runs()
    closed = ledger.close_stale_runs(500.5, **which)
    theirs = _snapshot()
    runs()
    assert rust_store.store("ledger", "close_stale_runs", now=500.5, **which) == closed
    assert _snapshot(clear=False) == theirs
    assert rust_store.store("ledger", "run_get", project_id="p", run_id="r1") == ledger.run_get("p", "r1")


def test_the_default_closes_runs_from_before_this_start(rust_store) -> None:
    ledger.run_put("p", _run("old", started=100.0))
    ledger.run_put("p", _run("ahead", started=9e12))
    assert rust_store.store("ledger", "close_stale_runs") == 1
    assert ledger.run_get("p", "old")["status"] == "interrupted" and ledger.run_get("p", "ahead")["status"] == "running"


def test_the_path_and_watch_indexes_are_replaced_the_same(rust_store) -> None:
    first = {"a.txt": {"hash": "h1", "n": 1}, "b/é.txt": {"hash": "h2", "list": [1.5, None]}, "c.txt": "bare"}
    second = {"a.txt": {"n": 1.0, "hash": "h1"}, "c.txt": {"now": "a doc"}, "d.txt": {}}
    seen1, seen2 = {"a.txt": "h1", "b/é.txt": "h2", "c.txt": "h3"}, {"a.txt": "h1", "c.txt": "h9", "d.txt": ""}
    for (was, index), (was_seen, seen) in zip(((({}), first), (first, second), (second, {})), (({}, seen1), (seen1, seen2), (seen2, {}))):
        def start() -> None:
            ledger.index_replace("p", was)
            ledger.watch_replace("p", was_seen)
            ledger.index_replace("other", first)

        start()
        ledger.index_replace("p", index)
        ledger.watch_replace("p", seen)
        theirs = _snapshot()
        start()
        rust_store.store("ledger", "index_replace", project_id="p", index=index)
        rust_store.store("ledger", "watch_replace", project_id="p", seen=seen)
        assert _snapshot(clear=False) == theirs
        assert rust_store.store("ledger", "index_get", project_id="p") == ledger.index_get("p") == index
        assert rust_store.store("ledger", "watch_get", project_id="p") == ledger.watch_get("p") == seen
    assert rust_store.store("ledger", "index_get", project_id="other") == first


def test_an_unchanged_index_row_is_not_rewritten(rust_store) -> None:
    ledger.index_replace("p", {"a.txt": {"n": 1, "hash": "h"}})
    stored = _sql("SELECT doc FROM path_index")
    rust_store.store("ledger", "index_replace", project_id="p", index={"a.txt": {"hash": "h", "n": 1.0}})
    assert _sql("SELECT doc FROM path_index") == stored


def test_blobs_are_named_kept_and_swept_the_same(rust_store) -> None:
    texts = ["", "plain", "é ✓ \U0001f986 text\nwith lines", "x" * 70_000]
    for text in texts:
        name = rust_store.store("ledger", "blob_put", text=text)
        assert name == ledger.blob_put(text) and len(name) == 16  # the second put is ignored
        assert rust_store.store("ledger", "blob_get", digest=name) == ledger.blob_get(name) == text
        assert rust_store.store("ledger", "blob_exists", digest=name) is True
    ours = _snapshot()["blobs"]
    for text in texts:
        ledger.blob_put(text)
    assert _snapshot(clear=False)["blobs"] == ours
    assert rust_store.store("ledger", "blob_put", text="named", digest="mine") == "mine" == ledger.blob_put("ignored", digest="mine")
    assert rust_store.store("ledger", "blob_put", text="empty name", digest="") == ledger.blob_put("empty name", digest="")
    assert ledger.blob_get("mine") == "named"
    assert rust_store.store("ledger", "blob_get", digest="") is None and rust_store.store("ledger", "blob_get", digest="none") is None
    assert rust_store.store("ledger", "blob_exists", digest="none") is False

    kept = [ledger.blob_put("before"), ledger.blob_put("after")]
    ledger.run_put("p", _run("r1", entries=[{"seq": 1, "path": "a", "before_blob": kept[0], "after_blob": kept[1]}]))
    before = _snapshot(clear=False)
    swept = ledger.sweep_blobs()
    theirs = _snapshot()
    for table, rows in before.items():
        for row in rows:
            _sql(f"INSERT INTO {table}({_TABLES[table] if table == 'blobs' else ', '.join(_columns(table))}{', created' if table == 'blobs' else ''}) "
                 f"VALUES ({', '.join('?' * (len(row) + (table == 'blobs')))})", *row, *([0.0] if table == "blobs" else []))
    assert rust_store.store("ledger", "sweep_blobs") == swept == len(texts) + 2
    assert _snapshot(clear=False) == theirs and [row[0] for row in theirs["blobs"]] == sorted(kept)


def _columns(table: str) -> list[str]:
    return [row[1] for row in _sql(f"SELECT * FROM pragma_table_info('{table}')")]
