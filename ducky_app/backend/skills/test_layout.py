"""The pack layout names other tools and IDEs rely on."""

from __future__ import annotations

from backend.skills import layout


def test_agent_skills_layout_names() -> None:
    assert (layout.PACK_FILE, layout.REFERENCES_DIR, layout.SKILL_PACKS_DIR) == ("SKILL.md", "references", "skill_packs")
    assert layout.EXPORT_FORMAT == "ducky-skill-pack" and layout.EXPORT_FORMAT_VERSION == 3


def test_id_patterns() -> None:
    assert layout._PACK_ID_RE.match("genre-rpg_2")
    assert not layout._PACK_ID_RE.match("2fast")
    assert not layout._SUBSKILL_ID_RE.match("Has Space")
    assert layout._PLAIN_SCALAR_RE.match("plain text")
    assert not layout._PLAIN_SCALAR_RE.match('needs "quotes"')
