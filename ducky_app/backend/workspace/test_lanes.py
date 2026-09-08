"""Lane globs (shared contract cases), overlap rules, resolution cache, and the policy."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from backend.workspace import identity, lanes
from backend.workspace.identity import RunContext
from backend.workspace.policy import WriteRequest

CASES = json.loads((Path(__file__).parent / "schemas" / "fixtures" / "lane_glob_cases.json").read_text(encoding="utf-8"))


@pytest.fixture(autouse=True)
def _clean():
    lanes.reset_for_tests()
    yield
    lanes.reset_for_tests()


@pytest.mark.parametrize("case", CASES["normalize"], ids=lambda c: c["input"])
def test_normalize_glob(case) -> None:
    assert lanes.normalize_glob(case["input"]) == case["output"]


@pytest.mark.parametrize("pattern", CASES["invalid"])
def test_invalid_patterns_are_rejected(pattern) -> None:
    with pytest.raises(lanes.LaneGlobError):
        lanes.normalize_glob(pattern)


@pytest.mark.parametrize("case", CASES["match"], ids=lambda c: f"{c['pattern']}~{c['path']}")
def test_match(case) -> None:
    assert lanes.match(case["pattern"], case["path"]) is case["expected"]


@pytest.mark.parametrize("case", CASES["overlap"], ids=lambda c: f"{c['a']}|{c['b']}")
def test_overlap(case) -> None:
    assert lanes.overlap(case["a"], case["b"]) == case["expected"]
    assert lanes.overlap(case["b"], case["a"]) == case["expected"]


def test_normalize_lane_accepts_text_and_lists() -> None:
    assert lanes.normalize_lane(None) is None
    assert lanes.normalize_lane([]) == []
    assert lanes.normalize_lane("Content/Verse/Hub\nContent/Verse/Shop/**, Content/Verse/Hub") == [
        "Content/Verse/Hub/**",
        "Content/Verse/Shop/**",
    ]


def test_check_lane_set_reports_pairs() -> None:
    verdict = lanes.check_lane_set(
        {
            "hacker": ["Content/Verse/Shop/**"],
            "artist": ["Content/Verse/**"],
            "reviewer": [],
            "architect": None,
            "wild": ["Content/*/Shop/**"],
        }
    )
    assert verdict["errors"] == ["hacker: Content/Verse/Shop/** overlaps artist: Content/Verse/** (contains)"]
    assert any("(maybe)" in w for w in verdict["warnings"])


def test_set_member_lane_rejects_overlap_and_applies_force() -> None:
    members = [
        {"member_conv_id": "hacker", "write_allowed": ["Content/Verse/Shop/**"]},
        {"member_conv_id": "artist", "write_allowed": None},
    ]
    with pytest.raises(ValueError, match="overlaps"):
        lanes.set_member_lane(members, "artist", ["Content/Verse/**"], set_by="user", now=1.0)
    with pytest.raises(ValueError, match="force=true"):
        lanes.set_member_lane(members, "artist", ["Content/*/Shop/**"], set_by="user", now=1.0)
    rows, warnings = lanes.set_member_lane(members, "artist", ["Content/*/Shop/**"], set_by="user", now=1.0, force=True)
    assert warnings and rows[1]["write_allowed"] == ["Content/*/Shop/**"] and rows[1]["lane_set_by"] == "user"
    rows, _ = lanes.set_member_lane(members, "artist", ["Content/Verse/Hub"], set_by="lead", now=2.0)
    assert rows[1]["write_allowed"] == ["Content/Verse/Hub/**"] and rows[1]["lane_set_at"] == 2.0
    rows, _ = lanes.set_member_lane(members, "artist", None, set_by="lead", now=3.0)
    assert rows[1]["write_allowed"] is None
    with pytest.raises(ValueError, match="not in this group"):
        lanes.set_member_lane(members, "ghost", [], set_by="user", now=1.0)


def test_lane_set_view_matches_schema() -> None:
    from backend.workspace.test_schemas import validate

    view = lanes.lane_set_view(
        "hub",
        [
            {"member_conv_id": "hacker", "name": "Hacker", "write_allowed": ["Content/Verse/Shop/**"], "lane_set_by": "u", "lane_set_at": 1.5},
            {"member_conv_id": "lead", "write_allowed": None},
        ],
    )
    assert validate("lane_set", view) == []
    assert view["lanes"]["hacker"]["set_by"] == "u" and view["lanes"]["lead"] == {"write_allowed": None}


class StaticProvider:
    def __init__(self, table):
        self.table = table
        self.calls = 0

    def lane_for(self, conv_id):
        self.calls += 1
        return self.table.get(conv_id)


def test_resolve_lane_uses_first_governing_provider_and_caches() -> None:
    p1 = StaticProvider({"a": ("Content/Verse/Shop/**",)})
    p2 = StaticProvider({"a": ("Content/Verse/Hub/**",), "b": ()})
    lanes.register_lane_provider(p1)
    lanes.register_lane_provider(p2)
    assert lanes.resolve_lane("a", now=100.0) == ("Content/Verse/Shop/**",)
    assert lanes.resolve_lane("b", now=100.0) == ()
    assert lanes.resolve_lane("c", now=100.0) is None
    assert lanes.resolve_lane("a", now=101.0) == ("Content/Verse/Shop/**",)
    assert p1.calls == 3  # a, b, c once each; the second "a" was cached
    p1.table["a"] = ("Content/Verse/Other/**",)
    assert lanes.resolve_lane("a", now=101.5) == ("Content/Verse/Shop/**",)
    assert lanes.resolve_lane("a", now=103.0) == ("Content/Verse/Other/**",)
    lanes.invalidate_lane_cache("a")
    assert lanes.resolve_lane("a", now=103.1) == ("Content/Verse/Other/**",) and p1.calls == 5


def _request(*paths: str, ctx: RunContext | None) -> WriteRequest:
    return WriteRequest(op="write", paths=paths, tool="workspace_write_file", ctx=ctx)


def test_policy_modes() -> None:
    laned = RunContext(run_id="r", conv_id="hacker", lane=("Content/Verse/Shop/**",), leader_conv_id="lead")
    inside = _request("Content/Verse/Shop/a.verse", ctx=laned)
    outside = _request("Content/Verse/Hub/a.verse", ctx=laned)
    enforce = lanes.LanePolicy(mode=lambda: "enforce")
    shadow = lanes.LanePolicy(mode=lambda: "shadow")
    off = lanes.LanePolicy(mode=lambda: "off")
    assert enforce.check(inside).allow and enforce.check(inside).policy == "lane"
    denied = enforce.check(outside)
    assert not denied.allow and denied.details["kind"] == "lane_denied" and "lead" in denied.reason
    flagged = shadow.check(outside)
    assert flagged.allow and flagged.shadow_violation and flagged.details["mode"] == "shadow"
    assert off.check(outside) is lanes.ALLOW
    assert enforce.check(_request("Content/Verse/Hub/a.verse", ctx=None)) is lanes.ALLOW
    unrestricted = RunContext(run_id="r", conv_id="lead")
    assert enforce.check(_request("Content/Verse/Hub/a.verse", ctx=unrestricted)) is lanes.ALLOW
    read_only = RunContext(run_id="r", conv_id="rev", lane=())
    assert not enforce.check(_request("Content/Verse/Hub/a.verse", ctx=read_only)).allow


def test_policy_resolves_lane_from_provider_when_context_has_none() -> None:
    lanes.register_lane_provider(StaticProvider({"hacker": ("Content/Verse/Shop/**",)}))
    ctx = RunContext(run_id="r", conv_id="hacker")
    assert not lanes.LanePolicy(mode=lambda: "enforce").check(_request("Content/Verse/Hub/a.verse", ctx=ctx)).allow


@pytest.mark.parametrize(
    ("tool", "args", "expected"),
    [
        ("workspace_write_file", {"relative_path": "Content\\Verse\\a.verse"}, ["Content/Verse/a.verse"]),
        ("mcp__uefn__workspace_write_file", {"relative_path": "Content/Verse/a.verse"}, ["Content/Verse/a.verse"]),
        ("ducky_call_tool", {"name": "workspace_write_file", "arguments": {"relative_path": "Content/Verse/a.verse"}}, ["Content/Verse/a.verse"]),
        ("create_project_verse_file", {"parent_relative": "Content/Verse/Shop", "name": "shop"}, ["Content/Verse/Shop/shop.verse"]),
        ("create_project_file", {"parent_relative": "Content/Verse", "name": "notes"}, ["Content/Verse/notes.txt"]),
        ("rename_project_entry", {"source_relative": "Content/Verse/Shop/a.verse", "new_name": "b.verse"}, ["Content/Verse/Shop/a.verse", "Content/Verse/Shop/b.verse"]),
        ("move_project_entry", {"source_relative": "Content/Verse/Shop/a.verse", "dest_parent_relative": "Content/Verse/Hub"}, ["Content/Verse/Shop/a.verse", "Content/Verse/Hub/a.verse"]),
        ("delete_project_entry", {"relative_path": "Content/Verse/Shop/a.verse"}, ["Content/Verse/Shop/a.verse"]),
        ("workspace_read_file", {"relative_path": "Content/Verse/a.verse"}, []),
        ("spawn_actor", {"actor_class": "x"}, []),
    ],
)
def test_extract_paths(tool, args, expected) -> None:
    assert lanes.extract_paths(tool, args)[1] == expected


def test_lane_block_reason_only_for_laned_context() -> None:
    assert lanes.lane_block_reason("workspace_write_file", {"relative_path": "Content/Verse/Hub/a.verse"}, mode=lambda: "enforce") is None
    token = identity.bind(RunContext(run_id="r", conv_id="hacker", lane=("Content/Verse/Shop/**",)))
    try:
        blocked = lanes.lane_block_reason("workspace_write_file", {"relative_path": "Content/Verse/Hub/a.verse"}, mode=lambda: "enforce")
        assert blocked is not None and not blocked.allow
        assert lanes.lane_block_reason("workspace_write_file", {"relative_path": "Content/Verse/Shop/a.verse"}, mode=lambda: "enforce") is None
        assert lanes.lane_block_reason("workspace_write_file", {"relative_path": "Content/Verse/Hub/a.verse"}, mode=lambda: "shadow") is None
    finally:
        identity.reset(token)
