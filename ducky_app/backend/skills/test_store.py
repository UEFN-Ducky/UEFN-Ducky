"""The ``skills.store`` shim: every old name still imports, and patches on it still land."""

from __future__ import annotations

from backend.skills import archive, authoring, discovery, manifest, ownership, store


def test_old_names_are_the_new_objects() -> None:
    assert store.load_pack_manifest is manifest.load_pack_manifest
    assert store.export_skill_pack_to_zip is archive.export_skill_pack_to_zip
    assert store.list_pack_ids is discovery.list_pack_ids
    assert store.plugin_owner_for_skill is ownership.plugin_owner_for_skill


def test_patching_the_shim_reaches_every_split_module(monkeypatch) -> None:
    def fake(pack_id: str) -> str:
        return "kit"

    real = ownership.plugin_owner_for_skill
    monkeypatch.setattr(store, "plugin_owner_for_skill", fake)
    for module in (archive, authoring, manifest, ownership):
        assert module.plugin_owner_for_skill is fake
    monkeypatch.undo()
    for module in (archive, authoring, manifest, ownership, store):
        assert module.plugin_owner_for_skill is real
