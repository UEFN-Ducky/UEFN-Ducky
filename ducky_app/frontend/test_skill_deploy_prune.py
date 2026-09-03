"""Deployed skill folders must never keep a stale file after a plugin update.

Covers the three ways a leftover used to survive:
  1. a file at the pack root from an older layout (only SKILL.md was rewritten),
  2. a reference file removed in the new version,
  3. a whole pack folder whose SKILL.md is gone/corrupt (managed tag unreadable).
And the guard: a user's own skill folder in the same root is never touched.
"""

from __future__ import annotations

import json
from pathlib import Path

import backend.skills.store as _skill
import frontend.skill_deploy as sd


def _managed_md(pack_id: str, body: str = "body") -> str:
    return (
        "---\n"
        f"name: {pack_id}\n"
        f'description: "{pack_id} pack"\n'
        "metadata:\n"
        f"  managed_by: {sd.MANAGED_BY}\n"
        "---\n\n"
        f"{body}\n"
    )


def _fake_pack(tmp_path: Path, pack_id: str, refs: dict[str, str]) -> Path:
    src = tmp_path / "src" / pack_id
    (src / _skill.REFERENCES_DIR).mkdir(parents=True, exist_ok=True)
    (src / _skill.PACK_FILE).write_text(_managed_md(pack_id), encoding="utf-8")
    for name, text in refs.items():
        (src / _skill.REFERENCES_DIR / name).write_text(text, encoding="utf-8")
    return src


def _wire(monkeypatch, tmp_path: Path, packs: dict[str, dict[str, str]]) -> None:
    srcs = {pid: _fake_pack(tmp_path, pid, refs) for pid, refs in packs.items()}
    monkeypatch.setattr(_skill, "list_pack_ids", lambda: list(packs))
    monkeypatch.setattr(sd, "_pack_source_dir", lambda pid: srcs.get(pid))
    monkeypatch.setattr(sd, "_deployed_skill_md", lambda pid: _managed_md(pid))


def test_pack_root_leftover_is_pruned(tmp_path, monkeypatch):
    root = tmp_path / "skills"
    stale = root / "verse" / "old_subskill.md"
    stale.parent.mkdir(parents=True)
    stale.write_text("wrong info from an older version", encoding="utf-8")
    (root / "verse" / _skill.PACK_FILE).write_text(_managed_md("verse"), encoding="utf-8")

    _wire(monkeypatch, tmp_path, {"verse": {"effects.md": "effects"}})
    logs = sd.deploy_skill_folders(root)

    assert not stale.exists(), "pack-root leftover survived the deploy"
    assert (root / "verse" / _skill.REFERENCES_DIR / "effects.md").is_file()
    assert any("Stale skill file pruned" in ln for ln in logs)


def test_removed_reference_is_pruned(tmp_path, monkeypatch):
    root = tmp_path / "skills"
    refs = root / "verse" / _skill.REFERENCES_DIR
    refs.mkdir(parents=True)
    (refs / "dropped.md").write_text("removed in the new version", encoding="utf-8")
    (root / "verse" / _skill.PACK_FILE).write_text(_managed_md("verse"), encoding="utf-8")

    _wire(monkeypatch, tmp_path, {"verse": {"effects.md": "effects"}})
    sd.deploy_skill_folders(root)

    assert not (refs / "dropped.md").exists()
    assert (refs / "effects.md").is_file()


def test_vanished_pack_pruned_even_with_unreadable_skill_md(tmp_path, monkeypatch):
    root = tmp_path / "skills"
    # First deploy records both packs in the ledger.
    _wire(monkeypatch, tmp_path, {"verse": {"a.md": "a"}, "gone": {"b.md": "b"}})
    sd.deploy_skill_folders(root)
    assert set(json.loads((root / sd.DEPLOY_LEDGER).read_text())["packs"]) == {"verse", "gone"}

    # The pack disappears AND its SKILL.md is corrupted, so the managed tag is
    # unreadable — the ledger is what still identifies the folder as ours.
    (root / "gone" / _skill.PACK_FILE).write_text("not frontmatter", encoding="utf-8")
    _wire(monkeypatch, tmp_path, {"verse": {"a.md": "a"}})
    logs = sd.deploy_skill_folders(root)

    assert not (root / "gone").exists(), "orphaned pack folder survived"
    assert (root / "verse").is_dir()
    assert any("Skill folder pruned" in ln for ln in logs)
    assert json.loads((root / sd.DEPLOY_LEDGER).read_text())["packs"] == ["verse"]


def test_user_authored_skill_folder_is_untouched(tmp_path, monkeypatch):
    root = tmp_path / "skills"
    mine = root / "my_own_skill"
    mine.mkdir(parents=True)
    (mine / _skill.PACK_FILE).write_text("---\nname: my_own_skill\n---\nmine\n", encoding="utf-8")
    (mine / "notes.md").write_text("keep me", encoding="utf-8")

    _wire(monkeypatch, tmp_path, {"verse": {"a.md": "a"}})
    sd.deploy_skill_folders(root)

    assert (mine / "notes.md").read_text(encoding="utf-8") == "keep me"
    assert (mine / _skill.PACK_FILE).is_file()


def test_unmanaged_folder_with_pack_id_keeps_its_extra_files(tmp_path, monkeypatch):
    """A user folder that happens to share a pack id keeps its own files."""
    root = tmp_path / "skills"
    clash = root / "verse"
    clash.mkdir(parents=True)
    (clash / _skill.PACK_FILE).write_text("---\nname: verse\n---\nuser copy\n", encoding="utf-8")
    (clash / "user_note.md").write_text("hand written", encoding="utf-8")

    _wire(monkeypatch, tmp_path, {"verse": {"a.md": "a"}})
    sd.deploy_skill_folders(root)

    assert (clash / "user_note.md").is_file(), "pruned a file we do not own"


def test_idempotent_second_deploy_touches_nothing(tmp_path, monkeypatch):
    root = tmp_path / "skills"
    _wire(monkeypatch, tmp_path, {"verse": {"a.md": "a"}})
    sd.deploy_skill_folders(root)
    logs = sd.deploy_skill_folders(root)
    assert logs == [], f"second deploy rewrote files: {logs}"
