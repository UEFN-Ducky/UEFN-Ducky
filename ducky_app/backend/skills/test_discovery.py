"""Where packs are found: AppData first, then the owning plugin, then the bundled copy."""

from __future__ import annotations

from pathlib import Path

import pytest

from backend.skills import discovery


def _pack(root: Path, pack_id: str) -> Path:
    p = root / pack_id
    (p / "references").mkdir(parents=True)
    (p / "SKILL.md").write_text(f"---\nname: {pack_id}\n---\n", encoding="utf-8")
    return p


def test_pack_roots_order_and_pack_ids(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    appdata, plugin, bundled = tmp_path / "appdata", tmp_path / "plugin", tmp_path / "bundled"
    _pack(appdata, "mine")
    plug = _pack(plugin, "kitpack")
    _pack(bundled, "shipped")
    (appdata / "not_a_pack").mkdir()
    monkeypatch.setattr(discovery, "appdata_skill_packs_dir", lambda: appdata)
    monkeypatch.setattr(discovery, "bundled_skill_packs_dir", lambda: bundled)
    monkeypatch.setattr(discovery, "_plugin_owned_skill_map", lambda: {"kitpack": plug})

    assert discovery._pack_roots("kitpack") == [appdata / "kitpack", plug, bundled / "kitpack"]
    assert discovery.list_pack_ids() == ["kitpack", "mine"]  # bundled packs are seeded, not listed
    assert discovery._is_bundled_pack_id("shipped") and not discovery._is_bundled_pack_id("mine")


def test_reference_files_follow_order_then_name(tmp_path: Path) -> None:
    refs = _pack(tmp_path, "p") / "references"
    (refs / "b.md").write_text("b", encoding="utf-8")
    (refs / "a.md").write_text("a", encoding="utf-8")
    (refs / "z.md").write_text("---\nmetadata:\n  order: 1\n---\nz", encoding="utf-8")
    assert [f.name for f in discovery._reference_files(tmp_path / "p")] == ["z.md", "a.md", "b.md"]


def test_bundled_dir_is_found_in_a_dev_checkout() -> None:
    bundled = discovery._bundled_skill_packs_dir()
    assert bundled is not None and bundled.name == "skill_packs" and bundled.parent.name == "frontend"


def test_ids_are_normalized() -> None:
    assert discovery.normalize_pack_id(" My Pack ") == "my_pack"
    assert discovery.normalize_subskill_id("Night Sky") == "night_sky"
    with pytest.raises(ValueError):
        discovery.normalize_pack_id("../etc")
