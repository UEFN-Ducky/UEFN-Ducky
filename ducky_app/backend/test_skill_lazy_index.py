"""Assert skill prompts are index-only and respect disabled_packs deny-lists."""

from __future__ import annotations

import io
import zipfile
from pathlib import Path

import pytest

from backend.skill import (
    SkillSelection,
    build_skill_prompt,
    import_skill_pack_from_bytes,
    list_pack_ids,
    list_skill_packs,
    load_pack_manifest,
    seed_skill_packs,
    set_active_disabled_packs,
    skill_read_subskill,
)


def test_list_skill_packs_omits_text_by_default() -> None:
    """Studio catalog must not read every markdown body up front."""
    seed_skill_packs()
    packs = list_skill_packs()
    assert packs, "expected seeded skill packs"
    for pack in packs:
        for sub in pack.get("subskills") or []:
            assert "text" not in sub, f"unexpected body for {pack['id']}/{sub.get('id')}"
    with_text = list_skill_packs(include_text=True)
    assert any("text" in (s or {}) for p in with_text for s in (p.get("subskills") or []))


def test_build_skill_prompt_keeps_core_lazy() -> None:
    seed_skill_packs()
    text = build_skill_prompt(SkillSelection(disabled_packs=[]))
    assert "skill_read_subskill" in text
    assert "Available skill packs" in text
    assert "[yours]=" in text or "[shipped]" in text or "[store]" in text or "[plugin]" in text
    # Full core SKILL.md bodies stay lazy — do not dump operator essays.
    assert "Never paste the contents of a file" not in text
    # Every non-denied pack id should appear with an origin tag.
    for pid in list_pack_ids():
        assert f"`{pid}` [" in text
    # Subskills are listed under packs (lazy index for non-always_on).
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
