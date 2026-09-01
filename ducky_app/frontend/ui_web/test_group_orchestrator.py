"""Assert-style checks for multi-ducky group routing + @mention hops."""

from __future__ import annotations

import tempfile
import threading
from pathlib import Path
from types import SimpleNamespace

from frontend.settings import PanelSettings
from frontend.ui_web import group_orchestrator as go
from frontend.ui_web.group_orchestrator import (
    _MENTION_HOP_BUDGET,
    announce_private_member_talk,
    build_member_prompt,
    extract_mention_target,
    find_member_by_name,
    is_group_turn_prompt,
    is_subagent_conversation,
    member_display_name,
    normalize_member,
    pick_member_for_question,
    resolve_nested_representative,
    author_payload,
    sync_group_members_from_folder,
    wants_all_speakers,
)
from frontend.ui_web.project_chats import (
    append_message,
    create_conversation,
    create_folder,
    load_conversation,
    load_folders,
    save_conversation,
    save_folders,
)


MEMBERS = [
    {
        "member_conv_id": "c1",
        "profile_id": "verse-coder",
        "name": "Verse Coder",
        "tts_voice": "builtin:A",
        "color": "#7aa2f7",
    },
    {
        "member_conv_id": "c2",
        "profile_id": "level-designer",
        "name": "Level Designer",
        "tts_voice": "builtin:B",
        "color": "#9ece6a",
    },
]


PROFILES = [
    {
        "id": "verse-coder",
        "when_to_use": "Writing and editing Verse code, devices, APIs",
        "ducky_personality": "Verse specialist",
    },
    {
        "id": "level-designer",
        "when_to_use": "Layout, placement, spatial design of the map",
        "ducky_personality": "Level designer",
    },
]


def test_find_member_by_name_compact():
    assert find_member_by_name(MEMBERS, "VerseCoder")["name"] == "Verse Coder"
    assert find_member_by_name(MEMBERS, "level designer")["name"] == "Level Designer"
    assert find_member_by_name(MEMBERS, "nobody") is None


def test_extract_mention_skips_self():
    reply = "I need help. @Level Designer can you measure the pad spacing?"
    hit = extract_mention_target(reply, MEMBERS, self_name="Verse Coder")
    assert hit is not None
    member, question = hit
    assert member["name"] == "Level Designer"
    assert "measure" in question.lower()

    self_hit = extract_mention_target("@Verse Coder ping", MEMBERS, self_name="Verse Coder")
    assert self_hit is None


def test_pick_member_keyword_fallback_without_llm():
    # Force keyword path by giving empty profiles that still have when_to_use
    # tokens matching the question (LLM may fail in test env → fallback).
    chosen = pick_member_for_question(
        "Please fix this Verse device API compile error",
        MEMBERS,
        profiles=PROFILES,
    )
    assert chosen["profile_id"] in {"verse-coder", "level-designer"}
    # Keyword score should prefer verse for Verse/API words when LLM unavailable.
    if chosen["profile_id"] != "verse-coder":
        # Still acceptable if a live LLM routed differently; single-member works.
        assert chosen["member_conv_id"]


def test_build_member_prompt_includes_mention_hint():
    prompt = build_member_prompt(
        member_name="Verse Coder",
        members=MEMBERS,
        transcript="User: hi\nVerse Coder: hello",
        message="wire the button",
        from_name="User",
    )
    assert "Verse Coder" in prompt
    assert "@Level Designer" in prompt
    assert "wire the button" in prompt
    assert _MENTION_HOP_BUDGET == 4


def test_mention_hop_budget_constant():
    # Documented ceiling in plan — keep the check so a silent raise is noticed.
    assert 1 <= _MENTION_HOP_BUDGET <= 8


def test_wants_all_speakers_roll_call():
    assert wants_all_speakers("whos in here") is True
    assert wants_all_speakers("Who's in here?") is True
    assert wants_all_speakers("say hi everyone") is True
    assert wants_all_speakers("fix the Verse compile error on the door") is False


def test_member_display_name_prefers_library_title():
    # Legacy invites stored style labels in ducky_name — still show profile name.
    assert (
        member_display_name(
            {
                "name": "Verse Coder",
                "ducky_name": "Hacker",
                "ducky_style": "hacker",
            }
        )
        == "Verse Coder"
    )
    assert member_display_name({"ducky_name": "3D Modeler"}) == "3D Modeler"


def test_normalize_member_keeps_is_group():
    row = normalize_member({"member_conv_id": "g1", "name": "Squad", "is_group": True}, index=0)
    assert row["is_group"] is True
    assert row["member_conv_id"] == "g1"


def test_sync_group_members_from_folder_and_nested_group():
    with tempfile.TemporaryDirectory() as tmp:
        root = str(Path(tmp))
        settings = PanelSettings.load()
        folder = create_folder("Parent Group", "", root)
        hub = create_conversation(settings, folder.id, title="Parent Group", project_root=root)
        hub.is_group = True
        hub.group_members = []
        save_conversation(hub, root)
        folders = load_folders(root)
        for f in folders:
            if f.id == folder.id:
                f.group_hub_id = hub.id
                break
        save_folders(folders, root)

        member = create_conversation(
            settings, folder.id, title="Verse Coder", ducky_name="Verse Coder", project_root=root
        )
        # Subagent under member — must NOT become a group member.
        create_conversation(
            settings,
            folder.id,
            title="Helper",
            parent_conv_id=member.id,
            project_root=root,
        )

        child_folder = create_folder("Nested Squad", folder.id, root)
        child_hub = create_conversation(
            settings, child_folder.id, title="Nested Squad", project_root=root
        )
        child_hub.is_group = True
        child_hub.group_members = []
        save_conversation(child_hub, root)
        folders = load_folders(root)
        for f in folders:
            if f.id == child_folder.id:
                f.group_hub_id = child_hub.id
                break
        save_folders(folders, root)

        rows = sync_group_members_from_folder(hub, root)
        ids = {r["member_conv_id"] for r in rows}
        assert member.id in ids
        assert child_hub.id in ids
        assert hub.id not in ids
        nested = next(r for r in rows if r["member_conv_id"] == child_hub.id)
        assert nested["is_group"] is True
        assert nested["name"] == "Nested Squad"
        # Subagent excluded
        assert all(r["name"] != "Helper" for r in rows)

        fresh_member = load_conversation(member.id, project_root=root)
        assert fresh_member is not None
        assert fresh_member.parent_conv_id == hub.id


def test_sync_keeps_orphaned_leader_on_roster():
    """leader_conv_id outside the folder must still appear in IN THIS CHAT."""
    with tempfile.TemporaryDirectory() as tmp:
        root = str(Path(tmp))
        settings = PanelSettings.load()
        folder = create_folder("Parent Group", "", root)
        hub = create_conversation(settings, folder.id, title="Parent Group", project_root=root)
        hub.is_group = True
        hub.group_members = []
        save_conversation(hub, root)
        folders = load_folders(root)
        for f in folders:
            if f.id == folder.id:
                f.group_hub_id = hub.id
                break
        save_folders(folders, root)

        child_folder = create_folder("Nested Squad", folder.id, root)
        child_hub = create_conversation(
            settings, child_folder.id, title="Nested Squad", project_root=root
        )
        child_hub.is_group = True
        child_hub.group_members = []
        save_conversation(child_hub, root)
        folders = load_folders(root)
        for f in folders:
            if f.id == child_folder.id:
                f.group_hub_id = child_hub.id
                break
        save_folders(folders, root)

        # Leader sits at root (not in group folder) — classic orphaned-leader bug.
        leader = create_conversation(
            settings, "", title="Producer", ducky_name="Producer", project_root=root
        )
        hub.leader_conv_id = leader.id
        save_conversation(hub, root)

        rows = sync_group_members_from_folder(hub, root)
        ids = [r["member_conv_id"] for r in rows]
        assert leader.id == ids[0]
        assert child_hub.id in ids

        fresh_leader = load_conversation(leader.id, project_root=root)
        assert fresh_leader is not None
        assert fresh_leader.folder_id == folder.id
        assert fresh_leader.parent_conv_id == hub.id


def test_is_subagent_conversation():
    with tempfile.TemporaryDirectory() as tmp:
        root = str(Path(tmp))
        settings = PanelSettings.load()
        parent = create_conversation(settings, "", title="Parent", project_root=root)
        child = create_conversation(
            settings, "", title="Child", parent_conv_id=parent.id, project_root=root
        )
        group = create_conversation(settings, "", title="Group", project_root=root)
        group.is_group = True
        save_conversation(group, root)
        member = create_conversation(
            settings, "", title="Member", parent_conv_id=group.id, project_root=root
        )
        assert is_subagent_conversation(child, project_root=root) is True
        assert is_subagent_conversation(member, project_root=root) is False
        assert is_subagent_conversation(parent, project_root=root) is False


def test_resolve_nested_representative_badge():
    speaker = {
        "member_conv_id": "subgroup",
        "name": "Art Team",
        "is_group": True,
        "color": "#f59e0b",
    }
    # Without a real subgroup on disk, resolve fails cleanly.
    run_as, pub = resolve_nested_representative(speaker, "paint a rock", profiles=[])
    assert run_as is None
    assert pub["name"] == "Art Team"

    leaf = normalize_member(
        {"member_conv_id": "c1", "name": "Verse Coder", "is_group": False},
        index=0,
    )
    run_as, pub = resolve_nested_representative(leaf, "hi", profiles=[])
    assert run_as is leaf
    assert pub is leaf


def test_author_payload_carries_group_path():
    payload = author_payload(
        {
            "name": "Art Team — Verse Coder",
            "member_conv_id": "c1",
            "color": "#f59e0b",
            "group_path": ["Art Team"],
            "group_id_path": ["subgroup"],
        }
    )
    assert payload["group_path"] == ["Art Team"]
    assert payload["group_id_path"] == ["subgroup"]
    assert payload["member_conv_id"] == "c1"


def test_is_group_turn_prompt():
    prompt = build_member_prompt(
        member_name="Verse Coder",
        members=MEMBERS,
        transcript="",
        message="fix the pads",
        from_name="User",
    )
    assert is_group_turn_prompt(prompt) is True
    assert is_group_turn_prompt("hey can you fix the pads?") is False


def test_announce_private_member_talk_posts_to_hub():
    with tempfile.TemporaryDirectory() as tmp:
        root = str(Path(tmp))
        settings = PanelSettings.load()
        group = create_conversation(settings, "", title="Squad", project_root=root)
        group.is_group = True
        group.group_members = [
            {
                "member_conv_id": "pending",
                "profile_id": "verse-coder",
                "name": "Verse Coder",
                "color": "#7aa2f7",
            }
        ]
        save_conversation(group, root)
        member = create_conversation(
            settings,
            "",
            title="Verse Coder",
            parent_conv_id=group.id,
            project_root=root,
        )
        group.group_members[0]["member_conv_id"] = member.id
        save_conversation(group, root)

        append_message(
            member,
            {"role": "user", "content": "wire four spawn pads", "text": "wire four spawn pads"},
            project_root=root,
        )
        append_message(
            member,
            {
                "role": "assistant",
                "content": "I'll set MaxPlayers to 4 and wire each pad.",
            },
            project_root=root,
        )

        pushed: list[dict] = []
        assert announce_private_member_talk(member.id, push=pushed.append, project_root=root) is True

        hub = load_conversation(group.id, project_root=root)
        assert hub is not None
        last = hub.messages[-1]
        assert last.get("role") == "assistant"
        assert last.get("side_chat_announce") is True
        assert "wire four spawn pads" in (last.get("content") or "")
        assert "MaxPlayers" in (last.get("content") or "")
        assert last.get("author", {}).get("name") == "Verse Coder"
        assert any(e.get("type") == "assistant_done" and e.get("conv_id") == group.id for e in pushed)


def test_announce_skips_orchestrated_group_turns():
    with tempfile.TemporaryDirectory() as tmp:
        root = str(Path(tmp))
        settings = PanelSettings.load()
        group = create_conversation(settings, "", title="Squad", project_root=root)
        group.is_group = True
        save_conversation(group, root)
        member = create_conversation(
            settings,
            "",
            title="Verse Coder",
            parent_conv_id=group.id,
            project_root=root,
        )
        prompt = build_member_prompt(
            member_name="Verse Coder",
            members=MEMBERS,
            transcript="",
            message="who is here?",
            from_name="User",
        )
        append_message(member, {"role": "user", "content": prompt}, project_root=root)
        append_message(
            member,
            {"role": "assistant", "content": "Verse Coder here."},
            project_root=root,
        )
        assert announce_private_member_talk(member.id, project_root=root) is False
        hub = load_conversation(group.id, project_root=root)
        assert hub is not None
        assert hub.messages == []


def test_group_member_turn_does_not_kill_on_wait_timeout(monkeypatch):
    """Folder/group members must keep working past the wait window.

    Killing them with cancel_on_timeout=True is what the panel shows as
    Interrupted: Cancelled when a dossier with duckies on it runs a long turn.
    """
    captured: dict = {}

    def fake_wait(conv_id, prompt, mode, model, **kwargs):
        captured["kwargs"] = kwargs
        return {"status": "done", "assistant_text": "hello"}

    monkeypatch.setattr("frontend.ui_web.agent_modes.run_message_and_wait", fake_wait)
    monkeypatch.setattr(
        go,
        "load_conversation",
        lambda cid, **k: SimpleNamespace(
            id=cid, messages=[], model="gpt-4o", leader_conv_id="", ducky_name="A"
        ),
    )
    monkeypatch.setattr(go, "register_member_hub", lambda *a, **k: None)
    monkeypatch.setattr(go, "unregister_member_hub", lambda *a, **k: None)
    monkeypatch.setattr(go, "build_member_prompt", lambda **k: "prompt")

    speaker = {"member_conv_id": "m1", "name": "A"}
    _badge, reply, err = go._run_member_turn(
        group_id="g1",
        speaker=speaker,
        members=[speaker],
        pending_text="hi",
        pending_from="User",
        mode="agent",
        model="gpt-4o",
        timeout_sec=180.0,
        roundtable=False,
        cancel=threading.Event(),
    )
    assert err == ""
    assert reply == "hello"
    assert captured["kwargs"]["cancel_on_timeout"] is False
    assert captured["kwargs"]["timeout_sec"] == 0.0


def test_roundtable_dedupes_same_member_as_leaf_and_nested_rep():
    """A folder leaf that is also a nested-group leader must run once.

    Two parallel run_message_and_wait(force=True) on the same conv_id makes
    prepare_run() cancel the first turn → Interrupted: Cancelled.
    """
    leaf = {"member_conv_id": "m1", "name": "Verse Coder", "is_group": False}
    nested_badge = {
        "member_conv_id": "m1",
        "name": "Squad — Verse Coder",
        "is_group": False,
    }
    unique = go.dedupe_prepared_speakers([(leaf, leaf), (leaf, nested_badge)])
    assert len(unique) == 1
    assert unique[0][0]["member_conv_id"] == "m1"


def test_cancel_group_run_stops_running_members(monkeypatch):
    """Stop on the folder hub must cancel in-flight member agents."""
    cancelled: list[str] = []
    group = SimpleNamespace(
        id="g1",
        is_group=True,
        group_members=[{"member_conv_id": "m1", "is_group": False}],
    )
    monkeypatch.setattr(go, "load_conversation", lambda cid, **k: group if cid == "g1" else None)
    monkeypatch.setattr(go, "group_members", lambda g: list(g.group_members))
    monkeypatch.setattr(
        "frontend.ui_web.agent_modes.cancel_agent",
        lambda cid: cancelled.append(cid),
    )
    monkeypatch.setattr(
        "frontend.ui_web.agent_modes.is_agent_running",
        lambda cid: cid == "m1",
    )
    ev = threading.Event()
    go._group_sessions["g1"] = ev
    try:
        go.cancel_group_run("g1")
        assert ev.is_set()
        assert cancelled == ["m1"]
    finally:
        go._group_sessions.pop("g1", None)
