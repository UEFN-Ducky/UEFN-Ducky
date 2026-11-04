"""The skills revision: a fingerprint of every pack's index sources on disk."""

from __future__ import annotations

import hashlib
import time

from backend.skills.discovery import _is_pack_dir, _pack_roots, list_pack_ids
from backend.skills.layout import PACK_FILE, REFERENCES_DIR

_SKILLS_REV: str = ""
_SKILLS_REV_AT: float = 0.0
_SKILLS_REV_TTL = 2.0

# A new chat's skill index by (skills revision, selection). Building it took ~150 ms with
# the GIL held on every create, so making groups or a team fast stalled the whole app.
_NEW_CHAT_SKILL_TEXT: dict[tuple[str, str], str] = {}


def skills_revision() -> str:
    """Fingerprint of the skill *index* sources on disk.

    A conversation freezes its skill prompt at creation (``skill_snapshot``) so
    toggling packs elsewhere cannot rewrite an in-flight chat. The cost is that
    a chat opened before an update never learns a new subskill exists —
    ``skill_read_subskill`` always reads bodies live, but the index the agent
    plans from is the old one, so it never thinks to load the new file.

    Comparing this against the revision stored on a conversation lets the send
    path rebuild that index exactly when the packs on disk changed, and never
    otherwise. Covers SKILL.md plus every reference file of every pack, so an
    added, renamed or removed subskill moves the revision. Memoized for a
    couple of seconds — the send path asks once per message.
    """
    global _SKILLS_REV, _SKILLS_REV_AT
    now = time.monotonic()
    if _SKILLS_REV and (now - _SKILLS_REV_AT) < _SKILLS_REV_TTL:
        return _SKILLS_REV
    h = hashlib.sha1()
    try:
        for pid in sorted(list_pack_ids()):
            h.update(pid.encode("utf-8", "replace"))
            root = next((r for r in _pack_roots(pid) if _is_pack_dir(r)), None)
            if root is None:
                continue
            for path in [root / PACK_FILE, *sorted((root / REFERENCES_DIR).glob("*.md"))]:
                try:
                    st = path.stat()
                except OSError:
                    continue
                h.update(f"{path.name}:{st.st_mtime_ns}:{st.st_size}".encode())
    except OSError:
        return _SKILLS_REV
    _SKILLS_REV = h.hexdigest()[:16]
    _SKILLS_REV_AT = now
    return _SKILLS_REV


def invalidate_skills_revision() -> None:
    """Drop the memoized revision (call after install/uninstall/seed)."""
    global _SKILLS_REV, _SKILLS_REV_AT
    _SKILLS_REV = ""
    _SKILLS_REV_AT = 0.0
    _NEW_CHAT_SKILL_TEXT.clear()


def _skills_changed() -> None:
    """A pack changed on disk: drop the memoized revision and re-index search soon."""
    invalidate_skills_revision()
    try:
        from backend.search import indexer

        indexer.request_refresh("skills-only")
    except Exception:  # noqa: BLE001 — search catches up on its next periodic check
        pass
