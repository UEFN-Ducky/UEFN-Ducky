"""The skill index is index-only, respects deny-lists, and reads sections lazily."""

from __future__ import annotations

import io
import zipfile
from pathlib import Path

import pytest

from backend.skills import discovery, revision, skill_index
from backend.skills.archive import import_skill_pack_from_bytes
from backend.skills.discovery import list_pack_ids
from backend.skills.manifest import load_pack_manifest
from backend.skills.seeding import seed_skill_packs
from backend.skills.selection import SkillSelection, set_active_disabled_packs
from backend.skills.skill_index import build_skill_prompt, skill_read_subskill


def test_build_skill_prompt_keeps_core_lazy() -> None:
    seed_skill_packs()
    text = build_skill_prompt(SkillSelection(disabled_packs=[]))
    assert "skill_read_subskill" in text
    assert "Available skill packs" in text
    assert "[yours]=" in text or "[shipped]" in text or "[store]" in text or "[plugin]" in text
    # Full core SKILL.md bodies stay lazy — do not dump operator essays.
    assert "Never paste the contents of a file" not in text
    from backend.skills.skill_index import _is_specialist_pack

    # Specialist blender packs collapse to one line unless the ducky opted in.
    for pid in list_pack_ids():
        if _is_specialist_pack(pid):
            assert f"`{pid}`" in text
        else:
            assert f"`{pid}` [" in text
    # Subskills are listed as id + tag only (no descriptions).
    assert "  - `core` [" in text


@pytest.fixture()
def isolated_appdata(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.setenv("HOME", str(tmp_path))
    return tmp_path


def test_build_skill_prompt_injects_always_on_bodies(isolated_appdata: Path) -> None:
    del isolated_appdata
    skill_md = (
        "---\nname: alwaysdemo\ndescription: demo\n"
        "metadata:\n  label: Always Demo\n  version: 1\n---\n\n"
        "# Core body that must stay lazy\n\nNever paste the contents of a file.\n"
    )
    ref_md = (
        "---\ndescription: hard rules\nmetadata:\n  label: Rules\n  always_on: true\n---\n\n"
        "# Injected rules\n\nAlways folder under Areas/demo/.\n"
    )
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("SKILL.md", skill_md)
        zf.writestr("references/area_rules.md", ref_md)
    result = import_skill_pack_from_bytes(
        buf.getvalue(), pack_id="alwaysdemo", replace=True, source="store", store_slug="alwaysdemo"
    )
    assert result.get("ok"), result
    man = load_pack_manifest("alwaysdemo")
    assert man is not None
    rules = next(s for s in man["subskills"] if s["id"] == "area_rules")
    assert rules.get("always_on") is True
    text = build_skill_prompt(SkillSelection(disabled_packs=[]))
    assert "Always-on skill rules" in text
    assert "alwaysdemo/area_rules" in text
    assert "Areas/demo/" in text
    # Core body still not dumped
    assert "Never paste the contents of a file" not in text


def test_disabled_packs_omitted_from_index_and_read() -> None:
    seed_skill_packs()
    packs = list_pack_ids()
    assert packs, "expected seeded skill packs"
    denied = packs[0]
    text = build_skill_prompt(SkillSelection(disabled_packs=[denied]))
    assert f"`{denied}`" not in text
    set_active_disabled_packs([denied])
    try:
        result = skill_read_subskill(denied, "core")
        assert result.startswith("ERROR: pack denied")
    finally:
        set_active_disabled_packs(None)


def test_the_mcp_tool_takes_skill_and_subskill_as_names_too() -> None:
    # Oct 11 2026: prompts show skill_read_subskill("ducky", "ai_plugins"); agents called it
    # with skill=/subskill= and every call failed validation.
    from backend.tools.verse.skill_tool import skill_read_subskill as tool

    by_alias = tool(skill="ducky", subskill="ai_plugins")
    assert not by_alias.startswith("ERROR"), by_alias[:200]
    assert by_alias == tool(pack_id="ducky", subskill_id="ai_plugins")
    assert tool(pack="ducky", subskill="ai_plugins") == by_alias  # Oct 16: a gpt-6-luna run guessed pack=
    assert tool().startswith("ERROR: pack_id is required")


# --- Section reads, the search line and pack blurbs ---


def _sky_pack() -> Path:
    root = discovery.appdata_skill_packs_dir() / "sky"
    (root / "references").mkdir(parents=True)
    (root / "SKILL.md").write_text(
        "---\nname: sky\ndescription: \"Sky: starfield and night sky materials for islands, with the hard rules\"\n"
        "metadata:\n  label: Sky\n---\n\n# Sky\n\n## Basics\nRead the recipe.\n",
        encoding="utf-8")
    (root / "references" / "starfield.md").write_text(
        "---\ndescription: Starfield recipe\nmetadata:\n  label: Starfield\n---\n\n"
        "## Night sky\nTwinkling stars.\n\n## Flags (HARD)\nTwo sided.\n```\n## not a heading\n```\n",
        encoding="utf-8")
    revision.invalidate_skills_revision()
    return root


def test_section_read_returns_only_that_heading() -> None:
    _sky_pack()
    text = skill_index.skill_read_subskill("sky", "starfield", section="night sky")
    assert text.startswith("## Sky — Starfield > Night sky")
    assert "Twinkling stars." in text and "Two sided." not in text
    flags = skill_index.skill_read_subskill("sky", "starfield", section="Flags")  # partial name matches
    assert "## not a heading" in flags


def test_a_file_too_big_for_one_read_names_the_rest() -> None:
    root = _sky_pack()
    parts = [f"## Part {n}\n" + f"Step {n} line.\n" * 400 for n in range(1, 6)]  # ~5.6k chars each
    (root / "references" / "big.md").write_text(
        "---\ndescription: Big\nmetadata:\n  label: Big\n---\n\n" + "\n".join(parts), encoding="utf-8")
    revision.invalidate_skills_revision()
    text = skill_index.skill_read_subskill("sky", "big")
    assert "Step 2 line." in text and "Step 3 line." not in text  # two whole parts fit, never half of one
    assert 'skill_read_subskill(pack_id="sky", subskill_id="big", section="<name>")' in text
    assert "- Part 3 (" in text and "- Part 5 (" in text
    assert "Step 5 line." in skill_index.skill_read_subskill("sky", "big", section="Part 5")
    assert "more than one read" not in skill_index.skill_read_subskill("sky", "starfield")  # small files whole


def test_unknown_section_lists_the_headings() -> None:
    _sky_pack()
    text = skill_index.skill_read_subskill("sky", "starfield", section="Nope")
    assert text.startswith("ERROR: no section 'Nope'")
    assert "- Night sky" in text and "- Flags (HARD)" in text


def test_prompt_index_mentions_skill_search_and_pack_blurbs() -> None:
    _sky_pack()
    full = skill_index.build_skill_prompt(SkillSelection())
    compact = skill_index.build_skill_prompt_compact(SkillSelection())
    assert 'skill_search("' in full and "skill_search(" in compact
    assert "`sky` [yours] — Sky: starfield and night sky materials" in full
    line = next(line for line in compact.splitlines() if line.startswith("- `sky`"))
    assert len(line) < 110 and line.endswith("…")


def test_blurb_handles_empty_label_prefix_and_block_scalars() -> None:
    assert skill_index._label_with_blurb("Sky", {"description": ""}, 50) == "Sky"
    assert skill_index._label_with_blurb("Sky", {"description": ">-"}, 50) == "Sky"
    assert skill_index._label_with_blurb("Sky", {"description": "Sky — night looks"}, 50) == "Sky: night looks"


def test_skill_search_hits_carry_only_what_an_agent_uses() -> None:
    # Scores, match details and ids already inside `read` were about a third of each hit.
    from backend.tools.verse.skill_tool import _lean

    row = {"kind": "section", "title": "Stars", "where": "Sky > Starfield > Stars", "snippet": "Make stars.",
           "score": 0.03, "pack": "sky", "subskill": "starfield", "section": "Stars", "plugin": "p",
           "read": 'skill_read_subskill(pack_id="sky", subskill_id="starfield", section="Stars")',
           "match": {"keyword_rank": 1, "meaning": 0.6}}
    assert _lean(row) == {"where": row["where"], "snippet": row["snippet"], "read": row["read"]}
    assert _lean({"kind": "tool", "tool": "spawn_actor", "snippet": "Place an actor.", "read": 'ducky_get_tools(name="spawn_actor")',
                  "where": ""}) == {"tool": "spawn_actor", "snippet": "Place an actor.", "read": 'ducky_get_tools(name="spawn_actor")'}


def test_new_chat_skill_text_is_built_once_per_revision(monkeypatch) -> None:
    # Oct 25 2026: ~150 ms per create with the GIL held, so making groups fast stalled the app.
    built: list[int] = []
    rev = {"value": "rev1"}
    monkeypatch.setattr(skill_index, "skills_revision", lambda: rev["value"])
    monkeypatch.setattr(skill_index, "build_skill_prompt", lambda sel: built.append(1) or f"INDEX {rev['value']}")
    revision._NEW_CHAT_SKILL_TEXT.clear()

    assert skill_index.conversation_skill_text(mutate=False) == "INDEX rev1"
    assert skill_index.conversation_skill_text(mutate=False) == "INDEX rev1"
    assert len(built) == 1
    rev["value"] = "rev2"  # a pack changed on disk: the next chat gets the new index
    assert skill_index.conversation_skill_text(mutate=False) == "INDEX rev2"
    revision.invalidate_skills_revision()
    skill_index.conversation_skill_text(mutate=False)
    assert len(built) == 3
