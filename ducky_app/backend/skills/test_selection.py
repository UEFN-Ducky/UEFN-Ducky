"""Skill selection: a deny-list of packs plus optional per-pack subskill allowlists."""

from __future__ import annotations

import pytest

from backend.skills import selection


@pytest.fixture(autouse=True)
def installed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(selection, "list_pack_ids", lambda: ["blender", "uefn", "verse"])


def test_enabled_packs_are_all_minus_denied() -> None:
    assert selection.SkillSelection(disabled_packs=["uefn"]).enabled_packs == ["blender", "verse"]
    assert selection.default_skill_selection().enabled_packs == ["blender", "uefn", "verse"]


def test_normalizers_drop_unknown_and_duplicate_ids() -> None:
    assert selection.normalize_disabled_packs(["UEFN", "uefn", "gone", "bad id!"]) == ["uefn"]
    assert selection.normalize_enabled_skills(["verse"]) == ["verse"]
    assert selection.normalize_enabled_subskills({"uefn": ["a", "a", " "], "": ["x"], "v": "no"}) == {"uefn": ["a"]}


def test_legacy_allowlist_becomes_a_deny_list() -> None:
    sel = selection.merge_selection(enabled_packs=["verse"], enabled_subskills={"verse": ["effects"]})
    assert sel.disabled_packs == ["blender", "uefn"]
    assert sel.enabled_subskills == {"verse": ["effects"]}


def test_conversation_selection() -> None:
    class Conv:
        disabled_packs = ["blender"]
        enabled_subskills = {"uefn": ["devices"]}

    class Legacy:
        disabled_packs = None
        enabled_subskills = {"uefn": ["devices"]}

    class Settings:
        default_disabled_packs = ["verse"]

    sel = selection.resolve_conversation_selection(Conv(), Settings())
    assert sel.disabled_packs == ["blender"] and sel.enabled_subskills == {"uefn": ["devices"]}
    legacy = selection.resolve_conversation_selection(Legacy(), Settings())
    assert legacy.disabled_packs == ["verse"] and legacy.enabled_subskills == {"uefn": ["devices"]}


def test_active_scopes() -> None:
    selection.set_active_disabled_packs(["uefn", " "])
    selection.set_active_enabled_subskills({"verse": ["effects"], "": ["x"]})
    try:
        assert selection.active_disabled_packs() == frozenset({"uefn"})
        assert selection.active_enabled_subskills() == {"verse": frozenset({"effects"})}
    finally:
        selection.set_active_disabled_packs(None)
        selection.set_active_enabled_subskills(None)
    assert selection.active_disabled_packs() == frozenset()
    assert selection.active_enabled_subskills() is None


def test_default_subskills_for_pack() -> None:
    man = {"subskills": [{"id": "core", "always_on": True}, {"id": "a", "default_enabled": True}, {"id": "b"}]}
    assert selection.default_subskills_for_pack(man) == ["core", "a"]
