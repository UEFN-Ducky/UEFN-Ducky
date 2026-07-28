"""Tests for fake-delegation detection (text/toolset based — not profile-name)."""

from __future__ import annotations

from types import SimpleNamespace

from backend.agent.delegation_guard import fake_delegation_warning, is_delegator_conv

_SWARM_PERSONALITY = (
    "Coordinate a swarm: ducky_group_create then ducky_group_invite specialists; "
    "follow up with ducky_send_chat_message."
)


def test_personality_swarm_tools_is_delegator():
    conv = SimpleNamespace(
        ducky_name="Anything",
        ducky_personality=_SWARM_PERSONALITY,
        builtin_toolsets=["builtin_ducky", "builtin_uefn"],
    )
    assert is_delegator_conv(conv)


def test_builtin_ducky_only_is_delegator():
    """Structural check: Ducky-app-only toolsets still count as coordinator."""
    conv = SimpleNamespace(
        ducky_name="Team Lead",
        ducky_personality="",
        builtin_toolsets=["builtin_ducky"],
    )
    assert is_delegator_conv(conv)


def test_specialist_without_swarm_text_is_not_delegator():
    conv = SimpleNamespace(
        ducky_name="Verse Coder",
        ducky_personality="Write Verse devices.",
        builtin_toolsets=["builtin_ducky", "builtin_uefn"],
    )
    assert not is_delegator_conv(conv)


def test_producer_name_alone_is_not_enough():
    """Name is irrelevant — empty details + full toolsets is not a delegator."""
    conv = SimpleNamespace(
        ducky_name="Producer",
        ducky_personality="",
        builtin_toolsets=["builtin_ducky", "builtin_uefn"],
    )
    assert not is_delegator_conv(conv)


def test_warns_when_claim_without_spawn():
    conv = SimpleNamespace(
        ducky_name="Lead",
        ducky_personality=_SWARM_PERSONALITY,
        builtin_toolsets=["builtin_ducky", "builtin_uefn"],
    )
    msg = {"content": "I'm delegating this to the right specialist now."}
    assert fake_delegation_warning(conv, msg, set())


def test_silent_when_spawn_called():
    conv = SimpleNamespace(
        ducky_name="Lead",
        ducky_personality=_SWARM_PERSONALITY,
        builtin_toolsets=["builtin_ducky", "builtin_uefn"],
    )
    msg = {"content": "Delegation complete."}
    assert fake_delegation_warning(conv, msg, {"ducky_spawn_chat"}) is None


def test_silent_when_group_invite_called():
    conv = SimpleNamespace(
        ducky_name="Lead",
        ducky_personality=_SWARM_PERSONALITY,
        builtin_toolsets=["builtin_ducky", "builtin_uefn"],
    )
    msg = {"content": "I'm seating the Verse Coder in the Programming group."}
    assert fake_delegation_warning(conv, msg, {"ducky_group_invite"}) is None
