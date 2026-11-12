"""Swarm routing: job kind → template profile + model."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from frontend import agent_profiles
from frontend import favorite_models as fm
from frontend import swarm_routing as sr

ONE_JOB_PER_ROW = {
    "narrative": "Narrative: quest dialogue",
    "research": "Research competitor islands",
    "concept": "Concept: art direction for the hub",
    "modeling": "Modeling: castle mesh in Blender",
    "material": "Texture and shader pass on rocks",
    "verse": "Code: gameplay round logic in Verse",
    "ui": "HUD: score widget",
    "level": "Level: arena layout",
    "npc": "NPC: creature patrols",
    "vfx": "VFX: Niagara sparks",
    "sound": "Sound: music and audio cues",
    "rig": "Rigging the dragon skeleton",
    "animation": "Animation: idle and run cycles",
    "video": "Video: launch trailer",
    "writing": "Writing: docs for the shop",
    "test": "QA: test the spawn pads",
    "review": "Review the changeset",
    "app": "Backend: SQLite store",
}


def test_every_row_has_a_job():
    assert set(ONE_JOB_PER_ROW) == {row.kind for row in sr.JOB_KINDS}


@pytest.mark.parametrize("kind,text", sorted(ONE_JOB_PER_ROW.items()))
def test_one_job_per_row(kind, text):
    row = sr.job_kind(kind)
    got = sr.route(text)
    assert got["kind"] == kind, got
    assert got["profile_id"] == row.profile_id
    assert got["model"] == row.model and got["model"]
    assert got["why"].startswith("matched")


def test_narrative_runs_on_developer_with_its_own_model():
    got = sr.route("Narrative: quest dialogue")
    assert got["kind"] == "narrative"
    assert got["profile_id"] == "developer"
    assert got["model"] == sr.job_kind("narrative").model


def test_explicit_kind_wins_over_keywords():
    got = sr.route("Narrative: quest dialogue", kind="Test")
    assert (got["kind"], got["profile_id"]) == ("test", "tester")
    assert "given" in got["why"]


def test_unknown_explicit_kind_keeps_its_name_on_developer():
    got = sr.route("anything", kind="localization")
    assert (got["kind"], got["profile_id"], got["model"]) == ("localization", "developer", "")


def test_no_match_falls_back_to_developer():
    got = sr.route("Tidy things up")
    assert (got["kind"], got["profile_id"]) == ("developer", "developer")


def test_python_job_routes_to_developer():
    assert sr.route("Python: settings migration")["profile_id"] == "developer"


def test_missing_specialist_profile_falls_back_to_developer():
    got = sr.route("VFX: Niagara sparks", profiles=[{"id": "developer", "favorite_models": []}])
    assert (got["kind"], got["profile_id"]) == ("vfx", "developer")
    assert got["model"] == sr.job_kind("vfx").model


def test_profile_own_model_beats_the_suggestion():
    profiles = [{"id": "tester", "favorite_models": ["codex:gpt-6-luna"]}]
    assert sr.route("QA: test it", profiles=profiles)["model"] == "codex:gpt-6-luna"


@pytest.fixture
def backends(monkeypatch):
    monkeypatch.setattr(fm, "known_backends", lambda: frozenset({"anthropic", "claude_code"}))
    monkeypatch.setattr(fm, "coding_agent_backends", lambda: frozenset({"claude_code"}))
    monkeypatch.setattr(fm, "billing_kind", lambda agent, provider, model="": "api_key")
    monkeypatch.setattr(fm, "_available_agent_models", lambda settings, backend="", **_k: {})


def _settings(**picks):
    return SimpleNamespace(swarm_job_models=picks, agent_api_keys=[])


def test_job_model_set_kind(backends):
    s = _settings(modeling="claude_code:claude-opus-5-5")
    assert sr.job_model("modeling", {"favorite_models": []}, s) == "claude_code:claude-opus-5-5"
    got = sr.route("Modeling: castle mesh", settings=s)
    assert got["model"] == "claude_code:claude-opus-5-5"


def test_job_model_bare_id_on_a_subscription(backends, monkeypatch):
    sel = fm.FavoriteSelection(backend="claude_code", model_id="claude-sonnet-5-5")
    monkeypatch.setattr(fm, "resolve_model_strict", lambda favs, settings: fm.ResolveOk(
        coding_agent="claude_code", model="claude-sonnet-5-5", provider="", selection=sel))
    s = _settings(research="claude-sonnet-5-5")
    assert sr.job_model("research", {"favorite_models": []}, s) == "claude-sonnet-5-5"


def test_job_model_unset_kind_is_empty(backends):
    assert sr.job_model("research", {"favorite_models": []}, _settings()) == ""


def test_job_model_unset_kind_uses_profile_model(backends):
    profile = {"favorite_models": ["claude_code:claude-opus-5-5"]}
    assert sr.job_model("research", profile, _settings()) == "claude_code:claude-opus-5-5"


def test_job_model_refuses_paid_api_model(backends):
    s = _settings(research="anthropic:claude-sonnet-5-5")
    assert sr.job_model("research", {"favorite_models": []}, s) == ""


def test_job_model_allows_paid_api_the_person_opened(backends):
    s = SimpleNamespace(swarm_job_models={"research": "anthropic:claude-sonnet-5-5"}, agent_api_keys=["anthropic"])
    assert sr.job_model("research", {}, s) == "anthropic:claude-sonnet-5-5"


def test_settings_normalizes_swarm_job_models():
    from frontend.settings import PanelSettings

    s = PanelSettings(swarm_job_models={" Modeling ": " codex:gpt-6-luna ", "bad": 3, "empty": " "})
    s.validate()
    assert s.swarm_job_models == {"modeling": "codex:gpt-6-luna"}
    s = PanelSettings(swarm_job_models=None)  # type: ignore[arg-type]
    s.validate()
    assert s.swarm_job_models == {}
    assert PanelSettings().to_json_dict()["swarm_job_models"] == {}


def test_developer_profile_is_bundled(monkeypatch):
    monkeypatch.setattr(agent_profiles, "_load_rows", lambda: [])
    ids = {p["id"] for p in agent_profiles.list_agent_profiles()}
    assert "developer" in ids
    dev = next(p for p in agent_profiles.list_agent_profiles() if p["id"] == "developer")
    assert dev["name"] == "Developer" and dev["disabled_packs"] == [] and dev["favorite_models"] == []


# --------------------------------------------------------------------------- detail level

REQUEST = [
    {"name": "Modeling", "task": "castle meshes", "kind": "modeling", "profile_id": "3d-modeler",
     "model": "claude_code:claude-sonnet-5-5", "paths": ["Content/Meshes/**"]},
    {"name": "Texture", "task": "stone materials", "kind": "texture", "profile_id": "material-artist",
     "model": "codex:gpt-6-luna", "paths": ["Content/Materials/**"]},
    {"name": "Verse", "task": "round logic", "kind": "verse", "profile_id": "verse-coder",
     "model": "claude_code:claude-opus-5-5", "paths": ["Content/Verse/**"]},
    {"name": "Narrative", "task": "quest dialogue", "kind": "narrative", "profile_id": "developer",
     "model": "claude_code:claude-opus-5-5", "paths": []},
]


def _names(jobs):
    return [j["name"] for j in jobs]


def test_detail_1_merges_each_area():
    jobs = sr.split_jobs(REQUEST, 1)
    assert _names(jobs) == ["Art", "Code", "Story"]
    art = jobs[0]
    assert art["kinds"] == ["modeling", "material"]
    assert art["task"] == "Modeling: castle meshes\nTexture: stone materials"
    assert art["paths"] == ["Content/Meshes/**", "Content/Materials/**"]
    assert (art["profile_id"], art["model"]) == ("3d-modeler", "claude_code:claude-sonnet-5-5")


def test_detail_2_keeps_the_request():
    assert sr.split_jobs(REQUEST, 2) == REQUEST
    assert sr.split_jobs(REQUEST, 2) is not REQUEST


def test_detail_3_splits_kinds_with_parts():
    jobs = sr.split_jobs(REQUEST, 3)
    assert _names(jobs) == [
        "Modeling: blockout", "Modeling: hero props", "Modeling: set dressing",
        "Texture: materials", "Texture: decals",
        "Verse: systems", "Verse: devices",
        "Narrative",
    ]
    for job in jobs[:3]:
        assert (job["profile_id"], job["model"], job["kind"]) == (
            "3d-modeler", "claude_code:claude-sonnet-5-5", "modeling")
        assert job["paths"] == ["Content/Meshes/**"]
    assert jobs[-1] == REQUEST[-1]
    assert all("group" not in j for j in jobs)


def test_detail_4_groups_parts_under_their_parent():
    level3 = sr.split_jobs(REQUEST, 3)
    jobs = sr.split_jobs(REQUEST, 4)
    assert _names(jobs) == _names(level3)
    assert [j.get("group") for j in jobs] == ["Modeling"] * 3 + ["Texture"] * 2 + ["Verse"] * 2 + [None]


def test_split_example_and_unknown_kinds():
    assert _names(sr.split_jobs([{"name": "Modeling", "kind": "modeling"}], 3)) == [
        "Modeling: blockout", "Modeling: hero props", "Modeling: set dressing"]
    odd = [{"name": "Localization", "kind": "localization"}]
    for level in (1, 2, 3, 4):
        assert sr.split_jobs(odd, level) == odd


def test_kind_from_the_job_text_and_clamped_level():
    jobs = sr.split_jobs([{"name": "HUD", "task": "score widget"}], 9)
    assert _names(jobs) == ["HUD: HUD", "HUD: menus"] and jobs[0]["group"] == "HUD"
    assert _names(sr.split_jobs([{"name": "Gameplay", "kind": "gameplay"}], 0)) == ["Code"]


def test_split_never_changes_the_input():
    before = [dict(j) for j in REQUEST]
    for level in (1, 2, 3, 4):
        sr.split_jobs(REQUEST, level)
    assert REQUEST == before


def test_a_job_falls_back_to_the_leads_model_when_its_usual_one_cant_run_here(monkeypatch):
    """Every kind's suggested model is a Claude name. A person with only a Gemini key was
    offered (and seated on) Claude: each invite failed and the lead then picked another program."""
    monkeypatch.setattr(fm, "known_backends", lambda: frozenset({"gemini", "claude_code"}))
    monkeypatch.setattr(fm, "coding_agent_backends", lambda: frozenset({"claude_code"}))
    monkeypatch.setattr("backend.agent.model_fetch.get_model_info", lambda p, m: None)
    monkeypatch.setattr(fm, "_available_api_models", lambda: {"gemini": {"gemini-3.5-flash"}})
    monkeypatch.setattr(fm, "_available_agent_models", lambda settings, backend="", **_k: {})
    settings = SimpleNamespace(swarm_job_models={}, agent_api_keys=[], default_model="")
    lead = {"lead_model": "gemini:gemini-3.5-flash", "lead_provider": "gemini"}

    assert not sr.can_run("claude-sonnet-5-5", settings, "gemini")
    assert sr.can_run("gemini:gemini-3.5-flash", settings, "gemini")
    assert not sr.can_run("gemini:gemini-3.5-flash", settings), "another lead's team may not use that key"
    got = sr.route("Modeling: build the pet meshes in Blender", settings=settings, profiles=[], **lead)
    assert (got["kind"], got["model"]) == ("modeling", "gemini:gemini-3.5-flash")
    # No lead known: the suggestion stays, as before.
    assert sr.route("Modeling: build the pet meshes", settings=settings, profiles=[])["model"] == "claude-sonnet-5-5"
    # Claude Code signed in here: the kind keeps its usual model, whoever leads.
    monkeypatch.setattr(fm, "_available_agent_models",
                        lambda settings, backend="", **_k: {"claude_code": {"claude-sonnet-5-5", "claude-opus-5-5"}})
    assert sr.route("Modeling: build the pet meshes", settings=settings, profiles=[], **lead)["model"] == "claude-sonnet-5-5"
