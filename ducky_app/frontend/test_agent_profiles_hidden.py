"""Empty hide-list is 'show all' — never expand it to every bundled id."""

from __future__ import annotations

from frontend.agent_profiles import (
    _heal_hidden_if_poisoned,
    _is_empty_or_poison,
    _stored_hidden_list,
    bundled_profile_ids,
    delete_agent_profile,
    list_agent_profiles,
    save_agent_profile_override,
)
from frontend.settings import PanelSettings


def test_empty_hidden_list_stays_empty() -> None:
    s = PanelSettings(hidden_bundled_agent_profile_ids=[])
    assert _stored_hidden_list(s) == []


def test_none_hidden_list_means_hide_all() -> None:
    s = PanelSettings(hidden_bundled_agent_profile_ids=None)
    assert set(_stored_hidden_list(s)) == set(bundled_profile_ids())


def test_poison_matches_save_one_then_hide_siblings() -> None:
    bundled = bundled_profile_ids()
    hidden = bundled - {"niagara-vfx"}
    assert _is_empty_or_poison(hidden, frozenset({"niagara-vfx"}), bundled)
    assert not _is_empty_or_poison(frozenset({"verse-coder"}), frozenset(), bundled)


def test_heal_restores_library_without_writing() -> None:
    bundled = bundled_profile_ids()
    s = PanelSettings(
        hidden_bundled_agent_profile_ids=sorted(bundled - {"niagara-vfx"}),
        agent_profile_overrides={"niagara-vfx": {"name": "Niagara VFX"}},
    )
    _heal_hidden_if_poisoned(s, persist=False)
    assert s.hidden_bundled_agent_profile_ids == []
    ids = {p["id"] for p in list_agent_profiles(s)}
    assert bundled <= ids


def test_saving_one_bundled_does_not_hide_the_rest(monkeypatch) -> None:
    s = PanelSettings(hidden_bundled_agent_profile_ids=[], agent_profile_overrides={})
    monkeypatch.setattr(PanelSettings, "load", classmethod(lambda cls: s))
    monkeypatch.setattr(s, "save", lambda: None)
    save_agent_profile_override("niagara-vfx", {"name": "Niagara VFX"})
    assert s.hidden_bundled_agent_profile_ids == []
    ids = {p["id"] for p in list_agent_profiles(s)}
    assert "niagara-vfx" in ids
    assert "verse-coder" in ids
    assert "level-designer" in ids


def test_deleting_one_bundled_hides_only_that_one(monkeypatch) -> None:
    s = PanelSettings(hidden_bundled_agent_profile_ids=[], agent_profile_overrides={})
    monkeypatch.setattr(PanelSettings, "load", classmethod(lambda cls: s))
    monkeypatch.setattr(s, "save", lambda: None)
    delete_agent_profile("verse-coder")
    assert s.hidden_bundled_agent_profile_ids == ["verse-coder"]
    ids = {p["id"] for p in list_agent_profiles(s)}
    assert "verse-coder" not in ids
    assert "niagara-vfx" in ids
    assert "level-designer" in ids
