"""A pack's manifest: built from SKILL.md + references, cached by stamp, bodies read lazily."""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from backend.skills import manifest
from backend.skills.seeding import seed_skill_packs
from backend.store.repos import kv


@pytest.fixture
def appdata(tmp_path: Path) -> Path:
    root = tmp_path / ".ducky-appdata" / "UEFN-Ducky"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _write_pack(appdata: Path, pack_id: str, skill_md: str) -> Path:
    pack = appdata / "skill_packs" / pack_id
    (pack / "references").mkdir(parents=True)
    (pack / "SKILL.md").write_text(skill_md, encoding="utf-8")
    (pack / "references" / "one.md").write_text("---\ndescription: One\n---\nx\n", encoding="utf-8")
    return pack


def test_manifest_lists_core_then_references_in_order(appdata: Path, monkeypatch) -> None:
    monkeypatch.setattr(manifest, "_is_plugin_owned_pack_id", lambda pid: False)
    pack = _write_pack(
        appdata, "demo",
        "---\nname: demo\ndescription: Demo pack\nlicense: MIT\nmetadata:\n  label: Demo\n  version: 3\n---\nbody\n",
    )
    (pack / "references" / "zero.md").write_text(
        "---\ndescription: Zero\nmetadata:\n  order: 1\n  always_on: true\n  load_condition: lights\n---\nz\n",
        encoding="utf-8",
    )
    man = manifest.load_pack_manifest("demo")
    assert man["label"] == "Demo" and man["kind"] == "custom" and man["license"] == "MIT"
    assert [s["id"] for s in manifest.list_subskills(man)] == ["core", "zero", "one"]
    zero = man["subskills"][1]
    assert zero["always_on"] is True and zero["load_condition"] == "lights"
    assert manifest.read_subskill_body("demo", "zero").strip() == "z"
    assert manifest.read_subskill_body("demo", "nope") is None
    assert manifest.load_pack_manifest("missing") is None


def test_list_skill_packs_omits_text_by_default() -> None:
    """Studio catalog must not read every markdown body up front."""
    seed_skill_packs()
    packs = manifest.list_skill_packs()
    assert packs, "expected seeded skill packs"
    for pack in packs:
        for sub in pack.get("subskills") or []:
            assert "text" not in sub, f"unexpected body for {pack['id']}/{sub.get('id')}"
    with_text = manifest.list_skill_packs(include_text=True)
    assert any("text" in (s or {}) for p in with_text for s in (p.get("subskills") or []))


def test_skill_manifest_cache_hits_until_a_reference_changes(appdata: Path, monkeypatch) -> None:
    pack = _write_pack(appdata, "demo", "---\nname: demo\ndescription: Demo pack\n---\nbody\n")
    monkeypatch.setattr(manifest, "_is_plugin_owned_pack_id", lambda pid: False)
    m1 = manifest.load_pack_manifest("demo")
    assert [s["id"] for s in m1["subskills"]] == ["core", "one"]
    assert kv.get_doc("cache_docs", "skill_manifest:demo") is not None
    calls = {"n": 0}
    real = manifest._manifest_from_skill_dir

    def counting(*a, **k):
        calls["n"] += 1
        return real(*a, **k)

    monkeypatch.setattr(manifest, "_manifest_from_skill_dir", counting)
    assert manifest.load_pack_manifest("demo") == m1 and calls["n"] == 0
    time.sleep(0.01)
    (pack / "references" / "two.md").write_text("---\ndescription: Two\n---\ny\n", encoding="utf-8")
    m2 = manifest.load_pack_manifest("demo")
    assert calls["n"] == 1 and [s["id"] for s in m2["subskills"]] == ["core", "one", "two"]


def test_store_installed_skill_pack_hits_the_manifest_cache(appdata: Path, monkeypatch) -> None:
    """A pack installed from the Store was cached under kind "store" but looked
    up as "custom", so every load re-parsed the whole pack and rewrote its row."""
    _write_pack(
        appdata, "shop-pack",
        "---\nname: shop-pack\ndescription: From the Store\nmetadata:\n  source: store\n---\nbody\n",
    )
    monkeypatch.setattr(manifest, "_is_plugin_owned_pack_id", lambda pid: False)
    parses: list[str] = []
    writes: list[str] = []
    real_parse = manifest._manifest_from_skill_dir
    real_set_doc = kv.set_doc
    monkeypatch.setattr(manifest, "_manifest_from_skill_dir", lambda *a, **k: (parses.append(a[2]), real_parse(*a, **k))[1])
    monkeypatch.setattr(kv, "set_doc", lambda t, key, v: (writes.append(key), real_set_doc(t, key, v)))
    first = manifest.load_pack_manifest("shop-pack")
    assert parses == ["store"]
    for _ in range(2):
        assert manifest.load_pack_manifest("shop-pack") == first
    assert parses == ["store"]
    assert writes == ["skill_manifest:shop-pack"]


def test_plugin_pack_defaults_to_no_redistribution(appdata: Path, monkeypatch) -> None:
    _write_pack(appdata, "kitpack", "---\nname: kitpack\ndescription: Kit\n---\nbody\n")
    monkeypatch.setattr(manifest, "_is_plugin_owned_pack_id", lambda pid: True)
    monkeypatch.setattr(manifest, "plugin_owner_for_skill", lambda pid: "kit")
    man = manifest.load_pack_manifest("kitpack")
    assert man["kind"] == "plugin" and man["source_plugin_id"] == "kit"
    assert man["license"] == "All Rights Reserved" and man["allow_redistribute"] is False


def test_subskill_paths() -> None:
    assert manifest._subskill_rel_path("SKILL.md") == "SKILL.md"
    assert manifest._subskill_rel_path("one.md") == "references/one.md"
    assert manifest._subskill_rel_path("references\\one.md") == "references/one.md"
    assert manifest.subskill_parent_id({"parent_id": ""}) is None
    assert manifest.list_root_subskills({"subskills": [{"id": "a"}, {"id": "b", "parent_id": "a"}]}) == [{"id": "a"}]
