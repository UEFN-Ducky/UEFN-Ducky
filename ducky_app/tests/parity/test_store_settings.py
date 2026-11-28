"""The settings and misc repos: what one backend writes to ducky.db, the other reads."""

from __future__ import annotations

import time

from backend.store import db
from backend.store.repos import misc, settings

from parity_harness import plain

_VALUES = {"theme": "dark", "n": 3, "ratio": 0.25, "tags": [1, {"a": None, "ok": True}], "name": 'é "q"\n'}
_DEFAULTS = {"theme": "light", "n": 3}


def _raw(key: str) -> str:
    return db.connect().execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()[0]


# --------------------------------------------------------------------------- settings


def test_rust_reads_the_settings_python_saved(rust_store) -> None:
    assert rust_store.store("settings", "has_rows") is False
    assert settings.save_fields(_VALUES, _DEFAULTS) == ["theme", "ratio", "tags", "name"]  # n is its default
    assert rust_store.store("settings", "load_fields") == settings.load_fields() == {
        k: v for k, v in _VALUES.items() if k != "n"
    }
    assert rust_store.store("settings", "has_rows") is settings.has_rows() is True


def test_python_reads_the_settings_rust_saved_and_the_rows_are_the_same_text(rust_store) -> None:
    saved = rust_store.store("settings", "save_fields", new_values=_VALUES, defaults=_DEFAULTS)
    assert saved == ["theme", "ratio", "tags", "name"]
    assert settings.load_fields() == rust_store.store("settings", "load_fields")
    by_rust = {key: _raw(key) for key in saved}
    settings.replace_all(_VALUES, _DEFAULTS)  # Python writes the same values over them
    assert {key: _raw(key) for key in saved} == by_rust


def test_a_value_back_at_its_default_deletes_the_row(rust_store) -> None:
    settings.save_fields({"theme": "dark"}, _DEFAULTS)
    assert rust_store.store("settings", "load_fields") == {"theme": "dark"}
    assert rust_store.store("settings", "save_fields", new_values={"theme": "light"}, defaults=_DEFAULTS) == ["theme"]
    assert settings.load_fields() == {} and settings.has_rows() is False
    # And the other way round.
    rust_store.store("settings", "save_fields", new_values={"theme": "dark"}, defaults=_DEFAULTS)
    assert settings.load_fields() == {"theme": "dark"}
    assert settings.save_fields({"theme": "light"}, _DEFAULTS) == ["theme"]
    assert rust_store.store("settings", "load_fields") == {}


def test_a_save_writes_only_what_its_caller_changed(rust_store) -> None:
    defaults = {"a": 0, "b": 0}
    # Rust loads, Python then changes `a`, Rust saves its older copy with a new `b`.
    rows, version = rust_store.store("settings", "load_fields_versioned")
    assert rows == {}
    settings.save_fields({"a": 1}, defaults)
    changed = rust_store.store("settings", "save_fields", new_values={"a": 0, "b": 2}, defaults=defaults, origin_version=version)
    assert changed == ["b"] and settings.load_fields() == {"a": 1, "b": 2}
    # Python loads, Rust changes `a`, Python saves its older copy with a new `b`.
    mine, version = settings.load_fields_versioned()
    rust_store.store("settings", "save_fields", new_values={"a": 5}, defaults=defaults)
    assert settings.save_fields({**mine, "b": 3}, defaults, origin_version=version) == ["b"]
    assert rust_store.store("settings", "load_fields") == {"a": 5, "b": 3}


def test_equal_numbers_of_another_type_are_not_a_change(rust_store) -> None:
    settings.save_fields({"on": True, "n": 2}, {})
    rust_store.store("settings", "load_fields")
    assert rust_store.store("settings", "save_fields", new_values={"on": 1, "n": 2.0}, defaults={}) == []
    assert settings.save_fields({"on": 1, "n": 2.0}, {}) == []


def test_the_old_ducky_fields_are_never_saved(rust_store) -> None:
    values = {name: [1] for name in settings.LEGACY_DUCKY_KEYS} | {"kept": 1}
    assert rust_store.store("settings", "save_fields", new_values=values, defaults={}) == ["kept"]
    assert settings.load_fields() == {"kept": 1}


def test_replace_all_sets_the_table_to_exactly_the_values(rust_store) -> None:
    settings.save_fields({"old": 1}, {})
    assert rust_store.store("settings", "replace_all", values=_VALUES, defaults=_DEFAULTS) is None
    expected = {k: v for k, v in _VALUES.items() if k != "n"}
    assert settings.load_fields() == expected
    settings.replace_all({"only": [1, 2]}, {})
    assert rust_store.store("settings", "load_fields") == {"only": [1, 2]}


# --------------------------------------------------------------------------- mcp servers


def test_mcp_servers_written_by_one_are_read_by_the_other(rust_store) -> None:
    assert rust_store.store("misc", "mcp_servers_present") is misc.mcp_servers_present() is False
    misc.mcp_servers_replace({"a": {"command": "x", "args": ["1"]}, "b": {"url": "http://h"}})
    assert rust_store.store("misc", "mcp_servers_get") == misc.mcp_servers_get()
    rust_store.store("misc", "mcp_servers_replace", servers={"b": {"url": "http://new"}, "c": {"command": "é"}})
    assert misc.mcp_servers_get() == {"b": {"url": "http://new"}, "c": {"command": "é"}}
    # An emptied map is still "a config exists".
    rust_store.store("misc", "mcp_servers_replace", servers={})
    assert misc.mcp_servers_get() == {} and misc.mcp_servers_present() is True
    assert rust_store.store("misc", "mcp_servers_present") is True


def test_an_empty_map_saved_by_rust_marks_the_config_as_seeded(rust_store) -> None:
    rust_store.store("misc", "mcp_servers_replace", servers={})
    assert misc.mcp_servers_present() is True


# --------------------------------------------------------------------------- captures


def test_captures_are_one_index_for_both(rust_store) -> None:
    for name in ("p1.png", "p2.png", "p3.png"):
        misc.capture_add(name, prefix="shot", nbytes=10)
        time.sleep(0.01)
    for name in ("r1.png", "r2.png"):
        rust_store.store("misc", "capture_add", filename=name, prefix="snip", nbytes=20)
        time.sleep(0.01)
    assert sorted(rust_store.store("misc", "capture_names")) == sorted(misc.capture_names())
    assert sorted(rust_store.store("misc", "capture_prune", keep=3)) == ["p1.png", "p2.png"]
    assert misc.capture_names() == {"p3.png", "r1.png", "r2.png"}
    assert sorted(misc.capture_prune(1)) == ["p3.png", "r1.png"]
    assert rust_store.store("misc", "capture_names") == ["r2.png"]
    row = db.connect().execute("SELECT prefix, bytes FROM captures WHERE filename='r2.png'").fetchone()
    assert tuple(row) == ("snip", 20)


# --------------------------------------------------------------------------- verse diagnostics

_FILES = {
    "a.verse": {"mtime_ns": 17, "size": 5, "errors": 1, "warnings": 0, "items": [{"line": 3, "msg": "é"}]},
    "b.verse": {"mtime_ns": 18, "size": 6, "errors": 0, "warnings": 2, "items": []},
}


def test_diagnostics_written_by_one_are_read_by_the_other(rust_store) -> None:
    assert rust_store.store("misc", "diag_stamp", project_id="p") is None and misc.diag_stamp("p") is None
    misc.diag_replace("p", _FILES)
    assert rust_store.store("misc", "diag_get", project_id="p") == misc.diag_get("p") == _FILES
    assert rust_store.store("misc", "diag_stamp", project_id="p") == plain(misc.diag_stamp("p"))
    # Rust rewrites: one file gone, one new with fields missing, one entry that is no object.
    files = {"b.verse": _FILES["b.verse"], "c.verse": {"errors": 4, "items": None}, "junk": 5}
    rust_store.store("misc", "diag_replace", project_id="p", files=files)
    expected = {"b.verse": _FILES["b.verse"], "c.verse": {"mtime_ns": 0, "size": 0, "errors": 4, "warnings": 0, "items": []}}
    assert misc.diag_get("p") == expected == rust_store.store("misc", "diag_get", project_id="p")
    assert rust_store.store("misc", "diag_get", project_id="other") == misc.diag_get("other") == {}
    assert rust_store.store("misc", "diag_stamp", project_id="p") == plain(misc.diag_stamp("p"))


def test_an_unchanged_diagnostics_file_is_not_rewritten(rust_store) -> None:
    misc.diag_replace("p", _FILES)
    before = misc.diag_stamp("p")
    time.sleep(0.01)
    rust_store.store("misc", "diag_replace", project_id="p", files=_FILES)
    assert misc.diag_stamp("p") == before


# --------------------------------------------------------------------------- verse digests


def test_digest_lines_indexed_by_one_are_found_by_the_other(rust_store) -> None:
    misc.digest_index("py.digest.verse", 11.5, ["creative_device := class:\n", "\n", "    OnBegin<override>()\r\n"])
    lines = ["player := class:\n", "   \n", '    GetName()<transacts> : "string"\n']
    rust_store.store("misc", "digest_index", path="rs.digest.verse", mtime=22.25, lines=lines)
    both = ["py.digest.verse", "rs.digest.verse"]
    for query, paths in [("class", both), ("OnBegin", both), ("getname", both), ('"string"', both),
                         ("class", ["rs.digest.verse"]), ("cl", both), ("class", []), ("  nothing here ", both)]:
        ours = rust_store.store("misc", "digest_search", query=query, paths=paths)
        theirs = plain(misc.digest_search(query, paths))
        assert sorted(ours) == sorted(theirs), query
    assert sorted(plain(misc.digest_search("class", both))) == [
        ["py.digest.verse", 1, "creative_device := class:"],
        ["rs.digest.verse", 1, "player := class:"],
    ]
    assert plain(misc.digest_search("GetName", both)) == [["rs.digest.verse", 3, '    GetName()<transacts> : "string"']]
    # Which of the two hits a limit of 1 keeps is SQLite's choice; that it keeps one is not.
    assert len(rust_store.store("misc", "digest_search", query="class", paths=both, limit=1)) == 1
    assert len(misc.digest_search("class", both, limit=1)) == 1
    for path in (*both, "missing"):
        assert rust_store.store("misc", "digest_indexed_mtime", path=path) == misc.digest_indexed_mtime(path)
    # Indexed again by the other backend: the old lines are replaced, not added to.
    misc.digest_index("rs.digest.verse", 30.0, ["only := class:\n"])
    assert rust_store.store("misc", "digest_search", query="player", paths=both) == []
    assert rust_store.store("misc", "digest_indexed_mtime", path="rs.digest.verse") == 30.0
