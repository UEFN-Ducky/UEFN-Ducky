"""Plugin ownership of skill packs: lookup, invalidate, disabled plugins."""

from __future__ import annotations

from pathlib import Path

import pytest

from backend.skills import ownership
from backend.skills.store import pass_memo
from backend.uefn_plugins import host as plugin_host
from backend.uefn_plugins import store as plugin_store


def _skill(root: Path, plugin_id: str, pack_id: str) -> dict[str, str]:
    path = root / plugin_id / "skills" / pack_id
    path.mkdir(parents=True)
    (path / "SKILL.md").write_text(f"---\nname: {pack_id}\ndescription: x\n---\n\nBody.\n", encoding="utf-8")
    return {"pack_id": pack_id, "path": str(path), "plugin_id": plugin_id}


@pytest.fixture()
def plugins(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict:
    state = {
        "rows": [_skill(tmp_path, "matkit", "materials"), _skill(tmp_path, "fxkit", "vfx")],
        "enabled": {"matkit"},
        "fingerprint": ("v1",),
        "listed": 0,
    }

    def list_rows():
        state["listed"] += 1
        return [dict(r) for r in state["rows"]]

    monkeypatch.setattr(plugin_store, "list_plugin_owned_skills", list_rows)
    monkeypatch.setattr(plugin_store, "appdata_uefn_plugins_dir", lambda: tmp_path)
    monkeypatch.setattr(plugin_store, "plugin_tree_fingerprint", lambda root: state["fingerprint"])
    monkeypatch.setattr(plugin_store, "get_enabled_plugin_ids", lambda: sorted(state["enabled"]))
    monkeypatch.setattr(plugin_host, "is_plugin_enabled", lambda pid: pid in state["enabled"])
    ownership.invalidate()
    yield state
    ownership.invalidate()


def test_lookup_names_owner_and_dir(plugins: dict) -> None:
    assert ownership.plugin_owner_for_skill("materials") == "matkit"
    assert ownership.plugin_owner_for_skill("nope") is None
    assert ownership._is_plugin_owned_pack_id("materials")
    assert set(ownership._plugin_owned_skill_map()) == {"materials"}
    assert ownership._plugin_owned_skill_map()["materials"].name == "materials"


def test_disabled_plugin_still_owns_but_is_not_loadable(plugins: dict) -> None:
    assert ownership.plugin_owner_for_skill("vfx") == "fxkit"
    assert "vfx" not in ownership._plugin_owned_skill_map()
    plugins["enabled"].add("fxkit")
    # Enabled ids are part of the cache key: no invalidate needed.
    assert "vfx" in ownership._plugin_owned_skill_map()


def test_one_cache_serves_every_lookup(plugins: dict) -> None:
    ownership.plugin_owner_for_skill("materials")
    ownership._plugin_owned_skill_map()
    ownership._is_plugin_owned_pack_id("vfx")
    assert plugins["listed"] == 1


def test_invalidate_drops_the_cache(plugins: dict) -> None:
    assert ownership.plugin_owner_for_skill("materials") == "matkit"
    plugins["rows"] = []
    assert ownership.plugin_owner_for_skill("materials") == "matkit"
    ownership.invalidate()
    assert ownership.plugin_owner_for_skill("materials") is None


def test_plugin_store_invalidate_clears_ownership(plugins: dict) -> None:
    ownership.plugin_owner_for_skill("materials")
    plugins["rows"] = []
    plugin_store.invalidate_plugin_skill_caches()
    assert ownership.plugin_owner_for_skill("materials") is None


def test_pass_memo_reads_once(plugins: dict) -> None:
    with pass_memo():
        ownership.plugin_owner_for_skill("materials")
        ownership.invalidate()  # even a dropped cache is not re-read inside one pass
        assert ownership._plugin_owned_skill_map().keys() == {"materials"}
    assert plugins["listed"] == 1


def test_missing_skill_file_is_owned_not_loadable(plugins: dict, tmp_path: Path) -> None:
    (Path(plugins["rows"][0]["path"]) / "SKILL.md").unlink()
    assert ownership.plugin_owner_for_skill("materials") == "matkit"
    assert "materials" not in ownership._plugin_owned_skill_map()


def test_broken_plugin_tree_means_no_owners(plugins: dict, monkeypatch: pytest.MonkeyPatch) -> None:
    def boom():
        raise OSError("gone")

    monkeypatch.setattr(plugin_store, "list_plugin_owned_skills", boom)
    ownership.invalidate()
    assert ownership.plugin_owner_for_skill("materials") is None
    assert ownership._plugin_owned_skill_map() == {}


def test_store_reexports_ownership_names() -> None:
    from backend.skills import store

    assert store.plugin_owner_for_skill is ownership.plugin_owner_for_skill
    assert store._plugin_owned_skill_map is ownership._plugin_owned_skill_map
