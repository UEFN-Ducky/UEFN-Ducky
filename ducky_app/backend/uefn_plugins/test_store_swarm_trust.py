"""The swarm grant 'Trust AI plugin drafts this swarm builds, for testing': ducky_plugin_test
from a granted swarm's seat turns on a draft those seats scaffolded with no needs_trust
stop; any other caller still gets needs_trust."""

from __future__ import annotations

import pytest

from backend.store import db
from backend.uefn_plugins import store
from backend.uefn_plugins.plugin_host.testing import patch_host
from backend.workspace import identity


@pytest.fixture
def ai_draft(tmp_path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.delenv("DUCKY_STORE_BACKEND", raising=False)
    monkeypatch.setattr(store, "appdata_dir", lambda: tmp_path / "app")
    monkeypatch.setattr(store, "load_plugin_manifest", lambda pid: {"id": pid, "source": "ai"})
    monkeypatch.setattr(store, "_defer_chat_enable_side_effects", lambda pid, enabled: None)
    patch_host(monkeypatch, "apply_plugin_enabled_change", lambda pid, enabled: None)
    patch_host(monkeypatch, "clear_plugin_crash", lambda pid: None)
    db.reset_for_tests()
    yield
    db.reset_for_tests()


def _as_seat(group: str):
    return identity.bind(identity.RunContext(conv_id=f"seat-{group}", group_id=group))


def test_a_granted_swarm_seat_tests_its_draft_without_needs_trust(ai_draft) -> None:
    from backend.tools.panel import panel_plugin_check as check

    store.set_swarm_draft_trust(["swarm", "swarm-nested"], True)
    token = _as_seat("swarm-nested")
    try:
        store.note_draft_creator("demo")
        out = check._turn_on("demo")
    finally:
        identity.reset(token)
    assert out.get("ok") is True and not out.get("needs_trust"), out
    assert "demo" not in store._trusted_local_ids()  # a test grant, not lasting trust


def test_without_the_grant_the_seat_gets_needs_trust(ai_draft) -> None:
    from backend.tools.panel import panel_plugin_check as check

    token = _as_seat("swarm")
    try:
        store.note_draft_creator("demo")
        assert check._turn_on("demo").get("needs_trust") is True
        store.set_swarm_draft_trust(["swarm"], True)
        assert store.swarm_trusts_draft("demo")
        store.set_swarm_draft_trust(["swarm"], False)  # switched off again
        assert check._turn_on("demo").get("needs_trust") is True
    finally:
        identity.reset(token)


def test_the_grant_covers_only_drafts_its_seats_made_and_only_its_seats(ai_draft) -> None:
    store.set_swarm_draft_trust(["swarm"], True)
    other = _as_seat("other-team")
    try:
        store.note_draft_creator("theirs")
        assert not store.swarm_trusts_draft("theirs")  # an ungranted seat
    finally:
        identity.reset(other)
    mine = _as_seat("swarm")
    try:
        assert not store.swarm_trusts_draft("theirs")  # made outside the swarm
        assert not store.swarm_trusts_draft("never-noted")
        assert store.set_uefn_plugin_enabled("theirs", True, swarm_test=True).get("needs_trust") is True
    finally:
        identity.reset(mine)
    assert not store.swarm_trusts_draft("theirs")  # the person (no seat) isn't a swarm caller
