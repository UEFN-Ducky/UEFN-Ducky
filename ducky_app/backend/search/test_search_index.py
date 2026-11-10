"""Indexing skill packs and tools, then ranking and filtering them (backend/search)."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from backend.search import indexer, query
from backend.store.repos import search as repo

DUCKY_APP = Path(__file__).resolve().parents[2]


def test_status_does_not_load_the_model(monkeypatch):
    from backend.search import embedder
    monkeypatch.setattr(embedder, '_loaded', None)
    monkeypatch.setattr(embedder, 'get_embedder', lambda: pytest.fail('status loaded the model'))
    result = indexer.status()
    assert result['ok'] and result['model_loaded'] is False
    assert embedder._loaded is None


def _appdata() -> Path:
    from frontend.app_paths import resolve_app_data_dir

    return resolve_app_data_dir()


def _write(root: Path, files: dict[str, str]) -> Path:
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return root


def _ref(label: str, desc: str, body: str, *, always_on: bool = False) -> str:
    extra = "  always_on: true\n" if always_on else ""
    return f"---\ndescription: {desc}\nmetadata:\n  label: {label}\n{extra}---\n\n{body}\n"


def _skill(label: str, desc: str, body: str) -> str:
    return f"---\nname: x\ndescription: {desc}\nmetadata:\n  label: {label}\n---\n\n{body}\n"


def _sky_pack() -> Path:
    return _write(_appdata() / "skill_packs" / "sky", {
        "SKILL.md": _skill("Sky", "Night sky looks for islands", "# Sky\n\n## Basics\nUse the recipes below."),
        "references/starfield.md": _ref(
            "Starfield", "Starfield material recipe",
            "## Night sky\nThe starfield recipe builds twinkling stars with no textures.\n\n## Flags\nTwo sided, unlit."),
        "references/rules.md": _ref("Rules", "Hard rules for the sky", "## Always\nNever use Niagara for stars.",
                                    always_on=True),
    })


def _plugin_pack(plugin_id: str = "oceanplug") -> Path:
    plugin = _appdata() / "uefn_plugins" / plugin_id
    _write(plugin, {"plugin.json": json.dumps({"id": plugin_id, "label": "Ocean Plug", "version": "1.0.0"})})
    return _write(plugin / "skills" / "ocean", {
        "SKILL.md": _skill("Ocean", "Ocean water materials and waves", "# Ocean\n\n## Waves\nBigger waves far from shore."),
    })


def _index() -> dict:
    from backend.uefn_plugins.store import invalidate_plugin_skill_caches

    invalidate_plugin_skill_caches()
    return indexer.index_packs(indexer.live_pack_sources())


EVERYTHING = query.Context()


def _keys(rows: list[dict]) -> list[str]:
    return [f"{r.get('pack')}/{r.get('subskill', 'core')}" for r in rows]


def test_search_finds_the_section_and_names_the_read_call() -> None:
    _sky_pack()
    _index()
    rows = query.search("starfield recipe twinkling stars", context=EVERYTHING, refresh=False)
    top = rows[0]
    assert (top["pack"], top["subskill"], top.get("section")) == ("sky", "starfield", "Night sky")
    assert top["read"] == 'skill_read_subskill(pack_id="sky", subskill_id="starfield", section="Night sky")'
    assert top["where"] == "Sky > Starfield > Night sky"
    # The pack row and its core sub-skill open with the same call; it is listed once.
    rows = query.search("sky night looks for islands", limit=20, context=EVERYTHING, refresh=False)
    reads = [r["read"] for r in rows]
    assert 'skill_read_subskill(pack_id="sky", subskill_id="core")' in reads and len(reads) == len(set(reads))


def test_unchanged_packs_are_skipped_and_edits_touch_only_their_rows() -> None:
    root = _sky_pack()
    first = _index()
    assert first["packs"] == 1
    assert _index()["skipped"] == 1
    (root / "references" / "rules.md").write_text(
        _ref("Rules", "Hard rules for the sky", "## Always\nNever use Niagara or particles for stars.", always_on=True),
        encoding="utf-8")
    stats = _index()
    assert stats["packs"] == 1 and stats["changed"] >= 1 and stats["added"] == 0


def test_removed_pack_is_pruned() -> None:
    import shutil

    root = _sky_pack()
    _index()
    shutil.rmtree(root)
    _index()
    assert repo.count_source("pack:sky") == 0
    assert query.search("starfield", context=EVERYTHING, refresh=False) == []


def test_denied_pack_and_subskill_allowlist_are_filtered_after_ranking() -> None:
    _sky_pack()
    _index()
    denied = query.Context(disabled_packs=frozenset({"sky"}))
    assert query.search("starfield night sky", context=denied, refresh=False) == []
    only_core = query.Context(enabled_subskills={"sky": frozenset()})
    keys = _keys(query.search("sky stars niagara recipe", limit=20, context=only_core, refresh=False))
    assert "sky/starfield" not in keys
    assert "sky/rules" in keys  # always_on stays readable


def test_plugin_pack_hides_while_plugin_is_off_without_reindex() -> None:
    _plugin_pack()
    _index()
    off = query.Context(plugin_enabled=lambda pid: False)
    on = query.Context(plugin_enabled=lambda pid: True)
    assert query.search("ocean waves", context=off, refresh=False) == []
    rows = query.search("ocean waves", context=on, refresh=False)
    assert rows and rows[0]["pack"] == "ocean" and rows[0]["plugin"] == "oceanplug"


def test_at_most_two_rows_per_subskill() -> None:
    body = "\n\n".join(f"## Stars {i}\nstars stars stars recipe {i}" for i in range(6))
    _write(_appdata() / "skill_packs" / "many", {
        "SKILL.md": _skill("Many", "Many star sections", "# Many"),
        "references/lots.md": _ref("Lots", "Lots of stars", body),
    })
    _index()
    rows = query.search("stars recipe", limit=10, context=EVERYTHING, refresh=False)
    assert sum(1 for r in rows if r.get("subskill") == "lots") <= query.PER_SUBSKILL


TOOLS = [
    {"name": "wire_verse_device_ref", "description": "Assign one placed device to a Verse @editable field.",
     "owner_label": "UEFN Ducky"},
    {"name": "spawn_actor", "description": "Place an actor in the level.", "owner_label": "UEFN Ducky"},
    {"name": "srv__ping", "description": "Ping the nested server.", "owner_label": "MCP server: srv"},
]


def test_tools_come_only_from_the_callers_catalog_and_exact_names_pin_first() -> None:
    indexer.index_tools(TOOLS, "rev1")
    rows = query.search_tools("wire a device into a verse field", {"spawn_actor"})
    assert all(r["tool"] == "spawn_actor" for r in rows)
    rows = query.search_tools("spawn_actor", {"spawn_actor", "wire_verse_device_ref"})
    assert rows[0]["tool"] == "spawn_actor"
    assert rows[0]["read"] == 'ducky_get_tools(name="spawn_actor")'
    # The name spelled in plain words pins it too.
    rows = query.search_tools("wire a verse device ref", {"spawn_actor", "wire_verse_device_ref"})
    assert rows[0]["tool"] == "wire_verse_device_ref"


def test_offline_nested_server_keeps_its_tool_rows(monkeypatch) -> None:
    monkeypatch.setattr(indexer, "_enabled_mcp_servers", lambda: {"srv"})
    indexer.index_tools(TOOLS, "rev1")
    indexer.index_tools(TOOLS[:1], "rev2")  # srv answered nothing this time; spawn_actor really left
    assert repo.find_doc("tool:srv__ping") is not None
    assert repo.find_doc("tool:spawn_actor") is None


def test_keyword_only_when_the_model_is_missing(monkeypatch) -> None:
    from backend.search import embedder

    monkeypatch.setattr(embedder, "get_embedder", lambda: None)
    _sky_pack()
    _index()
    rows = query.search("starfield recipe", context=EVERYTHING, refresh=False)
    assert rows and rows[0]["pack"] == "sky"
    assert rows[0]["match"]["meaning"] is None


def test_missing_vectors_are_filled_once_the_model_loads(monkeypatch) -> None:
    from backend.search import embedder

    if embedder.get_embedder() is None:
        pytest.skip("no search model folder")
    real = embedder.get_embedder
    monkeypatch.setattr(embedder, "get_embedder", lambda: None)
    _sky_pack()
    _index()
    monkeypatch.setattr(embedder, "get_embedder", real)
    assert indexer.fill_missing_vectors() == sum(repo.counts()["docs"].values())


def test_meaning_finds_a_paraphrase_keywords_miss() -> None:
    from backend.search import embedder

    if embedder.get_embedder() is None:
        pytest.skip("no search model folder")
    _write(_appdata() / "skill_packs" / "mix", {
        "SKILL.md": _skill("Mix", "Mixed topics", "# Mix"),
        "references/dome.md": _ref("Dome", "Celestial dome", "## Celestial dome\nstarfield twinkle galaxy constellation"),
        "references/pads.md": _ref("Pads", "Spawn pads", "## Spawn pads\nplayer spawn pad placement fairness"),
        "references/sea.md": _ref("Sea", "Ocean", "## Ocean\nocean water waves foam shore"),
    })
    _index()
    rows = query.search("night sky with stars", context=EVERYTHING, refresh=False)
    assert rows and rows[0]["subskill"] == "dome"


def test_two_processes_indexing_at_once_agree(tmp_path: Path) -> None:
    _sky_pack()
    _plugin_pack()
    code = (
        "from backend.uefn_plugins.store import invalidate_plugin_skill_caches\n"
        "from backend.search import indexer\n"
        "invalidate_plugin_skill_caches()\n"
        "indexer.index_packs(indexer.live_pack_sources())\n"
    )
    env = {**os.environ, "PYTHONPATH": str(DUCKY_APP)}
    procs = [subprocess.Popen([sys.executable, "-c", code], env=env, cwd=str(DUCKY_APP)) for _ in range(2)]
    assert [p.wait(120) for p in procs] == [0, 0]
    from backend.store import db

    db.reset_for_tests()
    once = repo.counts()["docs"]
    _index()
    assert repo.counts()["docs"] == once
    keys = [r[0] for r in db.connect().execute("SELECT doc_key FROM search_docs")]
    assert len(keys) == len(set(keys))


def test_background_refresh_indexes_when_switched_on(monkeypatch) -> None:
    monkeypatch.setenv("DUCKY_SEARCH_AUTOINDEX", "1")
    monkeypatch.setattr(indexer, "DEBOUNCE_S", 0.01)
    monkeypatch.setattr(indexer, "live_tool_rows", lambda: None)
    _sky_pack()
    indexer.request_refresh("test")
    assert indexer.wait_idle(30)
    assert repo.count_source("pack:sky") > 0
    status = indexer.status()
    assert status["ok"] and status["docs"]["section"] >= 1


def test_autoindex_switch_off_schedules_nothing(monkeypatch) -> None:
    monkeypatch.setenv("DUCKY_SEARCH_AUTOINDEX", "0")
    indexer.request_refresh("test")
    assert indexer._thread is None or not indexer._thread.is_alive()


def test_rebuild_wipes_rows() -> None:
    _sky_pack()
    _index()
    assert indexer.rebuild()["ok"]
    assert not repo.has_docs()


def test_the_worker_survives_a_failed_state_read(monkeypatch) -> None:
    # The read used to sit outside the try: the worker died with _running True, so
    # periodic checks stopped and the Database card said "Indexing" for good.
    monkeypatch.setenv("DUCKY_SEARCH_AUTOINDEX", "1")
    monkeypatch.setattr(indexer, "DEBOUNCE_S", 0.01)
    monkeypatch.setattr(indexer, "live_tool_rows", lambda: None)

    def broken(key):
        raise RuntimeError("database is locked")

    monkeypatch.setattr(repo, "get_state", broken)
    indexer.request_refresh("periodic")
    assert indexer.wait_idle(30)
    assert not indexer.is_running()


def test_tools_of_an_enabled_plugin_not_loaded_here_are_kept(monkeypatch) -> None:
    # A slow boot register, or a bridge started before the plugin was switched on,
    # lists the catalog without it; its rows must not be pruned until it is off.
    rows = [dict(TOOLS[1], plugin_id="uefn"), {"name": "meshy_balance", "description": "Credits left.", "plugin_id": "meshy"}]
    indexer.index_tools(rows, "rev1")
    monkeypatch.setattr(indexer, "_enabled_desktop_plugins", lambda: {"uefn", "meshy"})
    indexer.index_tools(rows[:1], "rev2")  # meshy still registering in this process
    assert repo.find_doc("tool:meshy_balance") is not None
    monkeypatch.setattr(indexer, "_enabled_desktop_plugins", lambda: {"uefn"})
    indexer.index_tools(rows[:1], "rev3")  # switched off: gone
    assert repo.find_doc("tool:meshy_balance") is None


def test_rebuild_keeps_plugin_records(monkeypatch) -> None:
    from backend.search import records

    monkeypatch.setattr(records, "_scope", lambda plugin_id: ("_local~personal", "personal"))
    _sky_pack()
    _index()
    records.SearchApi("wiki").upsert("p1", "Storm page", "storm circle settings")
    assert indexer.rebuild()["ok"]
    assert [r["id"] for r in records.SearchApi("wiki").query("storm circle")] == ["p1"]


def test_tools_of_a_plugin_that_failed_or_is_paused_here_are_pruned(monkeypatch) -> None:
    rows = [dict(TOOLS[1], plugin_id="uefn"), {"name": "team_tool", "description": "A team tool.", "plugin_id": "teamplug"}]
    indexer.index_tools(rows, "rev1")
    monkeypatch.setattr(indexer, "_enabled_desktop_plugins", lambda: {"uefn", "teamplug"})
    monkeypatch.setattr(indexer, "_tried_and_not_loaded", lambda: {"teamplug"})  # paused: lost team access
    indexer.index_tools(rows[:1], "rev2")
    assert repo.find_doc("tool:team_tool") is None


def _index_health(source_key: str) -> tuple[int, int, int]:
    """(rows of the source, rows of it with a vector, vectors left with no row); the keyword
    index (external content) must match its table."""
    from backend.store import db

    conn = db.connect()
    rows = conn.execute("SELECT count(*) FROM search_docs WHERE source_key=?", (source_key,)).fetchone()[0]
    vecs = conn.execute(
        "SELECT count(*) FROM search_vecs v JOIN search_docs d ON d.id = v.doc_id WHERE d.source_key=?",
        (source_key,)).fetchone()[0]
    orphans = conn.execute("SELECT count(*) FROM search_vecs WHERE doc_id NOT IN (SELECT id FROM search_docs)").fetchone()[0]
    conn.execute("INSERT INTO search_fts(search_fts, rank) VALUES('integrity-check', 1)")
    return rows, vecs, orphans


def test_installing_removing_and_reinstalling_a_plugin_keeps_the_index_and_vectors_in_step(monkeypatch) -> None:
    """Oct 18 2026 owner: "as we install and uninstall plugins with skills and tools, does the
    vector part of the db get updated properly?" Every step: rows, their vectors, no leftovers."""
    import shutil

    from backend.search import embedder

    if embedder.get_embedder() is None:
        pytest.skip("the search model is not in this checkout (build/fetch_search_model.py)")
    enabled = {"oceanplug"}
    monkeypatch.setattr(indexer, "_enabled_desktop_plugins", lambda: set(enabled))
    monkeypatch.setattr(indexer, "_tried_and_not_loaded", lambda: set())
    plugin_tools = [{"name": "ocean_make_waves", "description": "Make ocean waves on the island water.",
                     "plugin_id": "oceanplug"}]
    base_tools = [dict(t) for t in TOOLS]

    def search_ocean() -> list[dict]:
        return query.search("bigger ocean waves far from shore", context=EVERYTHING, refresh=False)

    # Installed: its skill pack and tool are indexed, each row with a vector, and found by meaning.
    root = _plugin_pack()
    _index()
    indexer.index_tools(base_tools + plugin_tools, "rev-installed")
    rows, vecs, orphans = _index_health("pack:ocean")
    assert rows > 0 and vecs == rows and orphans == 0
    hit = search_ocean()[0]
    assert hit["pack"] == "ocean" and (hit.get("match") or {}).get("meaning") is not None
    assert repo.find_doc("tool:ocean_make_waves") is not None

    # Uninstalled: its rows and their vectors go; nothing is left behind.
    shutil.rmtree(root.parent.parent)
    enabled.clear()
    _index()
    indexer.index_tools(base_tools, "rev-removed")
    assert _index_health("pack:ocean") == (0, 0, 0)
    assert repo.find_doc("tool:ocean_make_waves") is None
    assert not any(r.get("pack") == "ocean" for r in search_ocean())
    assert _index_health("tools")[2] == 0

    # Installed again: everything comes back with fresh vectors.
    _plugin_pack()
    enabled.add("oceanplug")
    _index()
    indexer.index_tools(base_tools + plugin_tools, "rev-again")
    rows, vecs, orphans = _index_health("pack:ocean")
    assert rows > 0 and vecs == rows and orphans == 0
    assert search_ocean()[0]["pack"] == "ocean"
    assert repo.find_doc("tool:ocean_make_waves") is not None
