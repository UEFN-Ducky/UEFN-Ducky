from pathlib import Path

import pytest

from backend.skills import store
from backend.skills.versions import next_skill_version, skill_version, skill_version_key


@pytest.mark.parametrize("raw, expected", [(7, 7), ("7", 7), ("1.0.0", "1.0.0"), (None, 0), ("bad", 0)])
def test_metadata_versions(raw, expected):
    assert skill_version(raw) == expected


def test_order_and_edit_versions():
    assert skill_version_key("1.0.10") > skill_version_key("1.0.9") > skill_version_key(99)
    assert next_skill_version("1.2.9") == "1.2.10"
    assert next_skill_version(9) == 10


def test_semver_pack_loads_in_agent_catalog_and_prompt(tmp_path: Path, monkeypatch):
    root = tmp_path / "art"
    root.mkdir()
    (root / "SKILL.md").write_text(
        "---\nname: art\ndescription: Art guidance\nmetadata:\n  version: 1.0.0\n---\n\nDraw.\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(store, "_pack_roots", lambda _pid: [root])
    monkeypatch.setattr(store, "list_pack_ids", lambda: ["art"])
    # Use the real frontmatter/manifest/catalog path that was crashing on installed skills.
    assert store.load_pack_manifest("art")["version"] == "1.0.0"
    assert store.list_skill_packs()[0]["version"] == "1.0.0"
    assert store.get_skill_pack_files("art")["version"] == "1.0.0"
    assert "`art`" in store.build_skill_prompt(store.SkillSelection())
    assert store._pack_version(root) == "1.0.0"


def test_export_metadata_keeps_newer_semver():
    meta = {"metadata": {"version": "1.0.9"}}
    store._apply_export_meta_commercial(meta, {"version": "1.0.10"})
    assert meta["metadata"]["version"] == "1.0.10"
    store._apply_export_meta_commercial(meta, {"version": 4})
    assert meta["metadata"]["version"] == "1.0.10"


def test_semver_import_export_and_edit(tmp_path: Path, monkeypatch):
    import io
    import json
    import zipfile

    monkeypatch.setattr(store, "appdata_skill_packs_dir", lambda: tmp_path / "packs")
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("SKILL.md", "---\nname: semver-demo\nmetadata:\n  version: 1.0.0\n---\n\nGuidance.\n")
    result = store.import_skill_pack_from_bytes(
        archive.getvalue(), pack_id="semver-demo", source="store", store_version="1.0.2",
    )
    assert result["ok"]
    assert store.load_pack_manifest("semver-demo")["version"] == "1.0.2"
    store._bump_pack_version("semver-demo")
    assert store.load_pack_manifest("semver-demo")["version"] == "1.0.3"
    exported = store.export_skill_pack_to_zip("semver-demo", tmp_path / "export.zip")
    with zipfile.ZipFile(exported) as zf:
        assert json.loads(zf.read("export.json"))["version"] == "1.0.3"
