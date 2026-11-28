"""Three small repos: usage calls, per-file version history, and the projects list."""

from __future__ import annotations

from backend.store.repos import history, projects, usage

from .parity_harness import callers, plain
from .test_store_chats import _sql

# --------------------------------------------------------------------------- usage

_CALL = {
    "ts": 100.5, "provider": "anthropic", "model": "m", "input_tokens": 10, "output_tokens": 20, "cache_read_tokens": 1,
    "cache_write_tokens": 2, "conv_id": "c1", "agent": "ducky", "ducky_label": "Dév", "cost_usd": 0.0125, "extra": "ignored",
}
_CALLS = [
    _CALL,
    {**_CALL, "ts": 200, "provider": "openai", "input_tokens": 0, "conv_id": "", "ducky_label": "", "cost_usd": 1},
    {**_CALL, "ts": 300.25, "cost_usd": None},
    {key: value for key, value in {**_CALL, "ts": 400.0}.items() if key != "cost_usd"},
]


def test_usage_calls_are_stored_and_read_the_same(rust_store) -> None:
    python, rust = callers(rust_store, "usage", usage)

    def calls(call) -> list:
        _sql("DELETE FROM usage_calls")
        for entry in _CALLS:
            call("insert", entry=entry)
        call("insert", entry={**_CALLS[0], "ts": 450.0}, retention_s=0)  # no retention: nothing goes
        before = _sql(f"SELECT {', '.join(usage.COLUMNS)} FROM usage_calls ORDER BY ts")
        call("insert", entry={**_CALLS[0], "ts": 500.0}, retention_s=250.0)  # the rows before 250 go
        return [before, _sql(f"SELECT {', '.join(usage.COLUMNS)} FROM usage_calls ORDER BY ts")]

    theirs = calls(python)
    assert calls(rust) == theirs and [len(rows) for rows in theirs] == [5, 4]
    for ts in (0.0, 300.25, 450.5, 999.0):
        assert rust("since", ts=ts) == usage.since(ts)
    assert rust("count") == usage.count() == 4
    assert ["cost_usd" in row for row in usage.since(0.0)] == [False, False, True, True]


# --------------------------------------------------------------------------- history

FILE = {"project_id": "p1", "path": "Content/a é.verse"}
_WHO = {"source": "agent", "run_id": "r1", "conv_id": None, "ducky_name": "Dév", "model": 0, "tool": "write", "unknown": "ignored"}


def _versions() -> dict:
    return {
        "versions": _sql("SELECT * FROM file_versions ORDER BY project_id, path, entry_id"),
        "blobs": _sql("SELECT hash, text, size FROM blobs ORDER BY hash"),
    }


def test_file_versions_are_stored_and_read_the_same(rust_store) -> None:
    python, rust = callers(rust_store, "history", history)

    def saved(call) -> list:
        _sql("DELETE FROM file_versions")
        _sql("DELETE FROM blobs")
        names = [
            call("put", entry_id="1000", content="first é\n", preview="first", saved_at=1000, attribution=_WHO, **FILE),
            call("put", entry_id="2000", content="second", preview="second", saved_at=2000, attribution={}, schema_version=3, **FILE),
            call("put", entry_id="2000", content="second, saved again", preview="again", saved_at=2001, attribution=_WHO, **FILE),
            call("put", entry_id="3000", content="first é\n", preview="same text", saved_at=3000, attribution=_WHO, **FILE),
            call("put", entry_id="4000", content="named", preview="", saved_at=4000, attribution=_WHO, content_hash="mine", **FILE),
            call("put", entry_id="1000", content="other file", preview="o", saved_at=1, attribution=_WHO, project_id="p1", path="b.verse"),
            call("put", entry_id="1000", content="other project", preview="o", saved_at=1, attribution=_WHO, project_id="p2", path=FILE["path"]),
        ]
        return [names, _versions()]

    theirs = saved(python)
    assert saved(rust) == theirs and theirs[0][0] == theirs[0][3] and theirs[0][4] == "mine"
    for file in (FILE, {"project_id": "p1", "path": "b.verse"}, {"project_id": "none", "path": "none"}):
        assert rust("newest", **file) == history.newest(**file)
        assert rust("list_rows", **file) == history.list_rows(**file)
        for entry_id in ("1000", "2000", "4000", "none"):
            assert rust("exists", entry_id=entry_id, **file) == history.exists(entry_id=entry_id, **file)
            assert rust("content", entry_id=entry_id, **file) == history.content(entry_id=entry_id, **file)
    assert history.content(entry_id="2000", **FILE) == "second, saved again" and history.newest(**FILE)["entry_id"] == "4000"


def test_a_file_keeps_its_newest_hundred_versions(rust_store) -> None:
    python, rust = callers(rust_store, "history", history)

    def many(call) -> dict:
        _sql("DELETE FROM file_versions")
        _sql("DELETE FROM blobs")
        for index in range(history.MAX_PER_FILE - 2):
            history.put(entry_id=f"{index:04d}", content=f"v{index}", preview="", saved_at=index, attribution={}, **FILE)
        for index in range(history.MAX_PER_FILE - 2, history.MAX_PER_FILE + 3):
            call("put", entry_id=f"{index:04d}", content=f"v{index}", preview="", saved_at=index, attribution={}, **FILE)
        return _versions()

    theirs = many(python)
    assert many(rust) == theirs
    assert len(theirs["versions"]) == history.MAX_PER_FILE and history.list_rows(**FILE)[-1]["entry_id"] == "0003"


# --------------------------------------------------------------------------- projects


def _projects() -> list:
    return _sql("SELECT id, path, name, deleted, kind, last_opened > 0 FROM projects ORDER BY id")


def test_projects_are_recorded_and_listed_the_same(rust_store, tmp_path) -> None:
    paths = [str(tmp_path / name) for name in ("Island One", "folder-é", "third", "never opened")]

    def rust(function: str, path: str | None = None, **args):
        if path is not None:
            args.update(slug=projects.slug_for(path), **({} if function == "kind_for" else {"path": path}))
        return rust_store.store("projects", function, **args)

    def python(function: str, path: str | None = None, **args):
        return getattr(projects, function)(*([] if path is None else [path]), **args)

    def opened(call) -> list:
        _sql("DELETE FROM projects")
        out = []

        def step(function: str, *args, **more) -> None:
            answer = call(function, *args, **more)
            out.append((function, None if function == "touch" else plain(answer), _projects()))

        step("has_rows")
        step("touch", paths[0], name="One", when=100.0, kind="uefn")
        step("touch", paths[1], when=200.0, kind="folder")
        step("touch", paths[2], when=150.0, kind="nonsense")  # no kind: an island
        step("touch", paths[1], name="Folder", when=300.0, kind="uefn")  # a folder may become an island
        step("touch", paths[0], when=250.0, kind="folder")  # an island never becomes a folder; its name stays
        step("touch", paths[2], when=50.0)
        step("recent_paths")
        step("recent_paths", limit=2)
        step("opened_rows")
        step("forget", paths[2])
        step("forget", paths[3])  # never opened: a removed row
        step("removed_paths")
        step("recent_paths")
        for path in paths:
            step("kind_for", path)
        step("touch", paths[2], when=400.0)  # opened again: back in the list
        step("opened_rows")
        step("has_rows")
        return out

    theirs = opened(python)
    for mine, yours in zip(opened(rust), theirs, strict=True):
        assert mine == yours
    assert projects.recent_paths() == [paths[2], paths[1], paths[0]] and projects.kind_for(paths[0]) == "uefn"
    assert projects.touch(paths[0], when=1.0) == projects.slug_for(paths[0])

    def imported(replace) -> list:
        _sql("DELETE FROM projects")
        projects.touch(paths[0], name="kept name", when=1.0, kind="folder")
        replace()
        return [projects.recent_paths(), _projects()]

    theirs = imported(lambda: projects.replace_recent(paths[1:] + paths[:1]))
    ours = imported(lambda: rust_store.store("projects", "replace_recent", projects=[[projects.slug_for(p), p] for p in paths[1:] + paths[:1]]))
    assert ours == theirs and theirs[0] == paths[1:] + paths[:1]
