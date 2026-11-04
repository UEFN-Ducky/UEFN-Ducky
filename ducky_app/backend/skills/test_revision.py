"""The skills revision moves when the index sources on disk change, and only then."""

from __future__ import annotations

from pathlib import Path

import pytest

from backend.skills import revision


@pytest.fixture()
def packs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    pack = tmp_path / "verse"
    (pack / "references").mkdir(parents=True)
    (pack / "SKILL.md").write_text("---\nname: verse\n---\n", encoding="utf-8")
    (pack / "references" / "effects.md").write_text("effects", encoding="utf-8")
    monkeypatch.setattr(revision, "list_pack_ids", lambda: ["verse"])
    monkeypatch.setattr(revision, "_pack_roots", lambda pid: [tmp_path / pid])
    revision.invalidate_skills_revision()
    yield pack
    revision.invalidate_skills_revision()


def test_revision_is_memoized_and_moves_with_a_new_reference(packs: Path) -> None:
    first = revision.skills_revision()
    assert first
    (packs / "references" / "cameras.md").write_text("new", encoding="utf-8")
    assert revision.skills_revision() == first, "memoized for a couple of seconds"
    revision.invalidate_skills_revision()
    assert revision.skills_revision() != first


def test_invalidate_drops_new_chat_texts(packs: Path) -> None:
    revision._NEW_CHAT_SKILL_TEXT[("rev", "sel")] = "INDEX"
    revision._skills_changed()
    assert revision._NEW_CHAT_SKILL_TEXT == {}
