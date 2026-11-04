from pathlib import Path

import pytest

from backend.skills import (
    archive,
    authoring,
    discovery,
    manifest,
    revision,
    seeding,
    selection,
    skill_index,
)
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
    for module in (discovery, manifest, revision):
        monkeypatch.setattr(module, "_pack_roots", lambda _pid: [root])
    for module in (discovery, manifest, revision, selection):
        monkeypatch.setattr(module, "list_pack_ids", lambda: ["art"])
    # Use the real frontmatter/manifest/catalog path that was crashing on installed skills.
    assert manifest.load_pack_manifest("art")["version"] == "1.0.0"
    assert manifest.list_skill_packs()[0]["version"] == "1.0.0"
    assert authoring.get_skill_pack_files("art")["version"] == "1.0.0"
    assert "`art`" in skill_index.build_skill_prompt(selection.SkillSelection())
    assert seeding._pack_version(root) == "1.0.0"


def test_export_metadata_keeps_newer_semver():
    meta = {"metadata": {"version": "1.0.9"}}
    archive._apply_export_meta_commercial(meta, {"version": "1.0.10"})
    assert meta["metadata"]["version"] == "1.0.10"
    archive._apply_export_meta_commercial(meta, {"version": 4})
    assert meta["metadata"]["version"] == "1.0.10"


def test_semver_import_export_and_edit(tmp_path: Path, monkeypatch):
    import io
    import json
    import zipfile

    for module in (discovery, archive, authoring, manifest, seeding):
        monkeypatch.setattr(module, "appdata_skill_packs_dir", lambda: tmp_path / "packs")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("SKILL.md", "---\nname: semver-demo\nmetadata:\n  version: 1.0.0\n---\n\nGuidance.\n")
    result = archive.import_skill_pack_from_bytes(
        buf.getvalue(), pack_id="semver-demo", source="store", store_version="1.0.2",
    )
    assert result["ok"]
    assert manifest.load_pack_manifest("semver-demo")["version"] == "1.0.2"
    authoring._bump_pack_version("semver-demo")
    assert manifest.load_pack_manifest("semver-demo")["version"] == "1.0.3"
    exported = archive.export_skill_pack_to_zip("semver-demo", tmp_path / "export.zip")
    with zipfile.ZipFile(exported) as zf:
        assert json.loads(zf.read("export.json"))["version"] == "1.0.3"
