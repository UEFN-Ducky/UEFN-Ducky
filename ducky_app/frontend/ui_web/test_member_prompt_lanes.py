"""Members learn their lane and their teammates' lanes; leaders are told to partition first."""

from __future__ import annotations

from frontend.ui_web.group_orchestrator import build_member_prompt, lane_prompt_bits

MEMBERS = [
    {"member_conv_id": "lead", "name": "Architect", "write_allowed": None},
    {"member_conv_id": "hacker", "name": "Hacker", "write_allowed": ["Content/Verse/Shop/**"]},
    {"member_conv_id": "artist", "name": "Artist", "write_allowed": ["Content/Verse/Hub/**"]},
    {"member_conv_id": "rev", "name": "Reviewer", "write_allowed": []},
]


def test_member_prompt_states_own_lane_and_teammates() -> None:
    prompt = build_member_prompt(
        member_name="Hacker",
        members=MEMBERS,
        transcript="",
        message="add a shop item",
        from_name="Architect",
        lane=["Content/Verse/Shop/**"],
        leader_name="Architect",
    )
    assert "Your write lane: Content/Verse/Shop/**" in prompt
    assert "ask @Architect to change it" in prompt
    assert "Artist → Content/Verse/Hub/**" in prompt and "Reviewer → (read-only)" in prompt
    assert "Hacker →" not in prompt


def test_read_only_and_unlaned_members() -> None:
    bits = lane_prompt_bits("Reviewer", MEMBERS, lane=[], leader_name="Architect")
    assert bits[0].startswith("Your write lane: (read-only: no file writes)")
    bits = lane_prompt_bits("Architect", MEMBERS, lane=None, is_leader=True)
    assert not any(b.startswith("Your write lane") for b in bits)
    assert bits and bits[0].startswith("Teammate lanes")


def test_leader_prompt_tells_them_to_partition_lanes() -> None:
    prompt = build_member_prompt(
        member_name="Architect",
        members=MEMBERS,
        transcript="",
        message="build the shop",
        from_name="User",
        is_leader=True,
    )
    assert "partition the work into disjoint write lanes" in prompt
    assert "ducky_spawn_chat(write_allowed=[...])" in prompt
    assert "changeset_list" in prompt


def test_prompt_without_lanes_is_unchanged_in_spirit() -> None:
    plain = [{"member_conv_id": "a", "name": "A"}, {"member_conv_id": "b", "name": "B"}]
    prompt = build_member_prompt(member_name="A", members=plain, transcript="", message="hi", from_name="User")
    assert "write lane" not in prompt and "Teammate lanes" not in prompt
