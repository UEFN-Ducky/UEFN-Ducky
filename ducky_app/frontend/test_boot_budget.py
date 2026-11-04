"""Boot budget: a skill deploy to every IDE root reads the plugin ownership map once.

After a boot with ``ship-newest`` the deploy burned 87% of a core for ~8 minutes:
``deploy_skill_folders`` ran once per skills root with no shared pass, and each pack
looked its owner up three times, so ``list_plugin_owned_skills`` ran ~450 times.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

import frontend.skill_deploy as sd
from backend.skills import discovery, ownership, seeding, skill_index
from backend.skills.layout import PACK_FILE, REFERENCES_DIR
from backend.uefn_plugins import store as plugin_store
from backend.uefn_plugins.plugin_host.testing import patch_host

PACKS = 50
ROOTS = 3
PLUGIN_PACKS = 5


def _write_pack(folder: Path, pack_id: str) -> None:
    refs = folder / REFERENCES_DIR
    refs.mkdir(parents=True, exist_ok=True)
    (folder / PACK_FILE).write_text(
        f"---\nname: {pack_id}\ndescription: {pack_id} tips\nmetadata:\n  label: {pack_id}\n---\n\nBody.\n",
        encoding="utf-8",
    )
    for ref in ("one", "two"):
        (refs / f"{ref}.md").write_text(
            f"---\ndescription: {ref}\nmetadata:\n  label: {ref}\n---\n\n{ref} body.\n",
            encoding="utf-8",
        )


@pytest.fixture()
def boot(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict:
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "appdata"))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.setenv("HOME", str(tmp_path))
    packs_dir = discovery.appdata_skill_packs_dir()
    for i in range(PACKS - PLUGIN_PACKS):
        _write_pack(packs_dir / f"pack{i:02d}", f"pack{i:02d}")

    plugins_dir = tmp_path / "plugins"
    rows = []
    for i in range(PLUGIN_PACKS):
        pack_id = f"plug{i}"
        skill_dir = plugins_dir / "kit" / "skills" / pack_id
        _write_pack(skill_dir, pack_id)
        rows.append({"pack_id": pack_id, "path": str(skill_dir), "plugin_id": "kit"})

    state = {"listed": 0}

    def list_rows():
        state["listed"] += 1
        return [dict(r) for r in rows]

    monkeypatch.setattr(plugin_store, "list_plugin_owned_skills", list_rows)
    monkeypatch.setattr(plugin_store, "appdata_uefn_plugins_dir", lambda: plugins_dir)
    monkeypatch.setattr(plugin_store, "plugin_tree_fingerprint", lambda root: ("v1",))
    monkeypatch.setattr(plugin_store, "get_enabled_plugin_ids", lambda: ["kit"])
    patch_host(monkeypatch, "is_plugin_enabled", lambda pid: pid == "kit")
    monkeypatch.setattr(discovery, "bundled_skill_packs_dir", lambda: None)
    monkeypatch.setattr(seeding, "seed_skill_packs", lambda force=False: [])
    monkeypatch.setattr(skill_index, "seed_skill_packs", lambda force=False: [])
    roots = {tmp_path / f"ide{i}" / sd.SKILLS_DIRNAME for i in range(ROOTS)}
    monkeypatch.setattr(sd, "registered_coding_agent_skills_roots", lambda: set(roots))
    state["roots"] = sorted(roots)
    ownership.invalidate()
    yield state
    ownership.invalidate()


def test_deploy_reads_ownership_once_per_pass(boot: dict) -> None:
    sd.sync_skill()
    assert boot["listed"] <= 1, f"list_plugin_owned_skills calls: {boot['listed']}"
    for root in boot["roots"]:
        deployed = {p.name for p in root.iterdir() if p.is_dir()}
        assert len(deployed) == PACKS
        md = (root / "plug0" / PACK_FILE).read_text(encoding="utf-8")
        assert "source_plugin_id: kit" in md
        assert f"managed_by: {sd.MANAGED_BY}" in md


def test_unchanged_redeploy_fits_boot_budget(boot: dict) -> None:
    sd.sync_skill()  # first deploy writes the folders
    boot["listed"] = 0
    ownership.invalidate()
    started = time.perf_counter()
    logs = sd.sync_skill()
    elapsed = time.perf_counter() - started
    assert boot["listed"] <= 1, f"list_plugin_owned_skills calls: {boot['listed']}"
    assert not [ln for ln in logs if "Skill folder ->" in ln], logs
    print(f"redeploy of {ROOTS}x{PACKS} packs: {elapsed:.3f}s")
    assert elapsed < 0.5, f"redeploy of {ROOTS}x{PACKS} packs took {elapsed:.2f}s"


def test_fast_path_still_restores_a_hand_edit(boot: dict) -> None:
    sd.sync_skill()
    ref = boot["roots"][0] / "pack00" / REFERENCES_DIR / "one.md"
    ref.write_text("edited by hand, a bit longer", encoding="utf-8")
    (ref.parent / "extra.md").write_text("stray", encoding="utf-8")
    logs = sd.sync_skill()
    assert "one body." in ref.read_text(encoding="utf-8")
    assert not (ref.parent / "extra.md").exists()
    assert [ln for ln in logs if "Skill folder ->" in ln] == [f"Skill folder -> {ref.parent.parent}"]


def test_lone_deploy_opens_its_own_pass(boot: dict) -> None:
    sd.deploy_skill_folders(boot["roots"][0])
    assert boot["listed"] <= 1, f"list_plugin_owned_skills calls: {boot['listed']}"
