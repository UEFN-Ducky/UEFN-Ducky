"""An open chat must learn about subskills added by a plugin update.

Every conversation freezes a skill *index* at creation so pack toggles cannot
rewrite an in-flight chat. Bodies are always read live, so content fixes land
immediately — but before this, a chat opened before an update never learned a
new subskill existed, so the agent never loaded it.
"""

from __future__ import annotations

from pathlib import Path

import backend.skills.store as store
import frontend.ui_web.project_chats as pc
from frontend.chat_store import Conversation


def _pack(root: Path, pack_id: str, refs: list[str]) -> Path:
    p = root / pack_id
    (p / store.REFERENCES_DIR).mkdir(parents=True, exist_ok=True)
    (p / store.PACK_FILE).write_text(
        f"---\nname: {pack_id}\ndescription: \"{pack_id}\"\n---\n\nbody\n", encoding="utf-8"
    )
    for name in refs:
        (p / store.REFERENCES_DIR / f"{name}.md").write_text(
            f"---\ndescription: \"{name}\"\n---\n\n{name} body\n", encoding="utf-8"
        )
    return p


def _wire(monkeypatch, root: Path, packs: dict[str, list[str]]) -> None:
    monkeypatch.setattr(store, "list_pack_ids", lambda: list(packs))
    monkeypatch.setattr(store, "_pack_roots", lambda pid: [root / pid])
    store.invalidate_skills_revision()


# --- skills_revision -------------------------------------------------------


def test_revision_is_stable_and_moves_with_the_index(tmp_path, monkeypatch):
    for pid, refs in {"verse": ["effects"]}.items():
        _pack(tmp_path, pid, refs)
    _wire(monkeypatch, tmp_path, {"verse": ["effects"]})
    first = store.skills_revision()
    assert first

    store.invalidate_skills_revision()
    assert store.skills_revision() == first, "revision moved with no change on disk"

    # A NEW subskill must move it — this is the case that was broken.
    (tmp_path / "verse" / store.REFERENCES_DIR / "cameras.md").write_text(
        "---\ndescription: \"cameras\"\n---\n\nnew\n", encoding="utf-8"
    )
    store.invalidate_skills_revision()
    assert store.skills_revision() != first


def test_revision_moves_when_a_subskill_is_removed(tmp_path, monkeypatch):
    _pack(tmp_path, "verse", ["effects", "doomed"])
    _wire(monkeypatch, tmp_path, {"verse": ["effects", "doomed"]})
    before = store.skills_revision()
    (tmp_path / "verse" / store.REFERENCES_DIR / "doomed.md").unlink()
    store.invalidate_skills_revision()
    assert store.skills_revision() != before


# --- sync_skill_snapshot ---------------------------------------------------


class _Settings:
    pass


def _conv(snapshot: str, rev: str) -> Conversation:
    return Conversation(
        id="c1", title="t", skill_snapshot=snapshot, skill_snapshot_rev=rev, disabled_packs=[]
    )


def test_stale_chat_is_rebuilt_and_restamped(tmp_path, monkeypatch):
    saved: list[Conversation] = []
    monkeypatch.setattr(pc, "save_conversation", lambda c, r=None, **k: saved.append(c))
    monkeypatch.setattr(store, "build_skill_prompt", lambda sel: "NEW INDEX with cameras")
    monkeypatch.setattr(store, "skills_revision", lambda: "rev2")

    conv = _conv("OLD INDEX", "rev1")
    assert pc.sync_skill_snapshot(conv, _Settings()) is True
    assert conv.skill_snapshot == "NEW INDEX with cameras"
    assert conv.skill_snapshot_rev == "rev2"
    assert conv.prompt_cache_snapshot is None, "prompt cache should be invalidated"
    assert saved, "refreshed conversation was not persisted"


def test_current_chat_is_left_alone(tmp_path, monkeypatch):
    saved: list[Conversation] = []
    monkeypatch.setattr(pc, "save_conversation", lambda c, r=None, **k: saved.append(c))
    monkeypatch.setattr(store, "build_skill_prompt", lambda sel: pytest_fail())
    monkeypatch.setattr(store, "skills_revision", lambda: "rev1")

    conv = _conv("INDEX", "rev1")
    assert pc.sync_skill_snapshot(conv, _Settings()) is False
    assert not saved, "rewrote a conversation whose skills had not changed"


def pytest_fail():  # pragma: no cover - only reached on regression
    raise AssertionError("build_skill_prompt called for an up-to-date chat")


def test_body_only_change_restamps_without_busting_the_cache(tmp_path, monkeypatch):
    """A fixed subskill body moves the revision but not the index."""
    monkeypatch.setattr(pc, "save_conversation", lambda c, r=None, **k: None)
    monkeypatch.setattr(store, "build_skill_prompt", lambda sel: "SAME INDEX")
    monkeypatch.setattr(store, "skills_revision", lambda: "rev2")

    conv = _conv("SAME INDEX", "rev1")
    conv.prompt_cache_snapshot = {"blocks": {"skill": "x"}}
    assert pc.sync_skill_snapshot(conv, _Settings()) is False
    assert conv.skill_snapshot_rev == "rev2", "must restamp or it rebuilds every send"
    assert conv.prompt_cache_snapshot is not None, "needless cache miss on a body-only fix"


def test_per_chat_selection_survives_the_refresh(tmp_path, monkeypatch):
    monkeypatch.setattr(pc, "save_conversation", lambda c, r=None, **k: None)
    monkeypatch.setattr(store, "skills_revision", lambda: "rev2")
    seen: dict = {}

    def _build(sel):
        seen["disabled"] = list(getattr(sel, "disabled_packs", []) or [])
        return "NEW INDEX"

    monkeypatch.setattr(store, "build_skill_prompt", _build)

    conv = _conv("OLD", "rev1")
    conv.disabled_packs = ["blender"]
    pc.sync_skill_snapshot(conv, _Settings())
    assert seen["disabled"] == ["blender"], "per-chat pack selection was lost"


def test_chat_without_a_snapshot_is_untouched(tmp_path, monkeypatch):
    monkeypatch.setattr(pc, "save_conversation", lambda c, r=None, **k: None)
    monkeypatch.setattr(store, "skills_revision", lambda: "rev2")
    conv = _conv("", "")
    assert pc.sync_skill_snapshot(conv, _Settings()) is False


def test_blank_rebuild_never_wipes_a_working_snapshot(tmp_path, monkeypatch):
    monkeypatch.setattr(pc, "save_conversation", lambda c, r=None, **k: None)
    monkeypatch.setattr(store, "build_skill_prompt", lambda sel: "   ")
    monkeypatch.setattr(store, "skills_revision", lambda: "rev2")
    conv = _conv("GOOD INDEX", "rev1")
    assert pc.sync_skill_snapshot(conv, _Settings()) is False
    assert conv.skill_snapshot == "GOOD INDEX"
