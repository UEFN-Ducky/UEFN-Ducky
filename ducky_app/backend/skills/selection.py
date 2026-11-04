"""Which packs and subskills an agent may use: the deny-list and per-pack allowlists."""

from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any

from backend.skills.discovery import list_pack_ids, normalize_pack_id
from backend.skills.manifest import list_root_subskills


@dataclass
class SkillSelection:
    """Deny-list of skill packs + optional per-pack subskill allowlists.

    Empty disabled_packs = every installed pack is available.
    enabled_subskills[pack] missing = all references for that pack; present = only those ids
    (always_on / core still readable).
    """

    disabled_packs: list[str] = field(default_factory=list)
    enabled_subskills: dict[str, list[str]] = field(default_factory=dict)

    @property
    def enabled_packs(self) -> list[str]:
        """Packs available to the agent (all installed minus denied)."""
        denied = set(self.disabled_packs)
        return [pid for pid in list_pack_ids() if pid not in denied]


_ACTIVE_DISABLED_PACKS: ContextVar[frozenset[str] | None] = ContextVar(
    "uefn_ducky_active_disabled_skill_packs", default=None
)
_ACTIVE_ENABLED_SUBSKILLS: ContextVar[dict[str, frozenset[str]] | None] = ContextVar(
    "uefn_ducky_active_enabled_subskills", default=None
)


def set_active_disabled_packs(pack_ids: list[str] | None) -> None:
    """Scope denied skill packs to the current agent run (None = no deny)."""
    if pack_ids is None:
        _ACTIVE_DISABLED_PACKS.set(None)
        return
    _ACTIVE_DISABLED_PACKS.set(frozenset(str(p) for p in pack_ids if str(p).strip()))


def active_disabled_packs() -> frozenset[str]:
    return _ACTIVE_DISABLED_PACKS.get() or frozenset()


def set_active_enabled_subskills(selection: dict[str, list[str]] | None) -> None:
    """Scope per-pack subskill allowlists for the current agent run (None = no filter)."""
    if not selection:
        _ACTIVE_ENABLED_SUBSKILLS.set(None)
        return
    out: dict[str, frozenset[str]] = {}
    for pack_id, ids in selection.items():
        key = str(pack_id or "").strip()
        if not key or not isinstance(ids, list):
            continue
        out[key] = frozenset(str(x).strip() for x in ids if str(x).strip())
    _ACTIVE_ENABLED_SUBSKILLS.set(out or None)


def active_enabled_subskills() -> dict[str, frozenset[str]] | None:
    return _ACTIVE_ENABLED_SUBSKILLS.get()


def normalize_disabled_packs(pack_ids: list[str] | None) -> list[str]:
    if not pack_ids:
        return []
    available = set(list_pack_ids())
    out: list[str] = []
    seen: set[str] = set()
    for raw in pack_ids:
        try:
            pid = normalize_pack_id(str(raw))
        except ValueError:
            continue
        if pid not in available or pid in seen:
            continue
        seen.add(pid)
        out.append(pid)
    return out


def normalize_enabled_packs(pack_ids: list[str] | None) -> list[str]:
    """Legacy allowlist normalizer — kept for callers that still pass pack lists."""
    if not pack_ids:
        return []
    available = set(list_pack_ids())
    out: list[str] = []
    seen: set[str] = set()
    for raw in pack_ids:
        try:
            pid = normalize_pack_id(raw)
        except ValueError:
            continue
        if pid not in available or pid in seen:
            continue
        seen.add(pid)
        out.append(pid)
    return out


def default_subskills_for_pack(manifest: dict[str, Any]) -> list[str]:
    """Legacy helper — studio/UI may still show default_enabled badges."""
    out: list[str] = []
    for sub in list_root_subskills(manifest):
        sid = str(sub.get("id", ""))
        if not sid:
            continue
        if sub.get("always_on") or sub.get("default_enabled", False):
            out.append(sid)
    return out


def default_skill_selection() -> SkillSelection:
    """All installed packs available (empty deny-list)."""
    return SkillSelection(disabled_packs=[])


def normalize_enabled_subskills(
    enabled_subskills: dict[str, list[str]] | None,
) -> dict[str, list[str]]:
    if not isinstance(enabled_subskills, dict):
        return {}
    out: dict[str, list[str]] = {}
    for pack_id, ids in enabled_subskills.items():
        key = str(pack_id or "").strip()
        if not key or not isinstance(ids, list):
            continue
        seen: set[str] = set()
        cleaned: list[str] = []
        for raw in ids:
            sid = str(raw or "").strip()
            if not sid or sid in seen:
                continue
            seen.add(sid)
            cleaned.append(sid)
        out[key] = cleaned
    return out


def merge_selection(
    enabled_packs: list[str] | None = None,
    enabled_subskills: dict[str, list[str]] | None = None,
    *,
    disabled_packs: list[str] | None = None,
) -> SkillSelection:
    """Build a SkillSelection from deny-list and optional per-pack subskill allowlists.

    When enabled_packs is provided (legacy UI), convert to disabled_packs = all − enabled.
    """
    if disabled_packs is None and enabled_packs is not None:
        available = set(list_pack_ids())
        allowed = set(normalize_enabled_packs(enabled_packs))
        disabled_packs = sorted(available - allowed)
    return SkillSelection(
        disabled_packs=normalize_disabled_packs(disabled_packs),
        enabled_subskills=normalize_enabled_subskills(enabled_subskills),
    )


def default_selection_from_settings(settings: Any) -> SkillSelection:
    disabled = list(getattr(settings, "default_disabled_packs", None) or [])
    return SkillSelection(disabled_packs=normalize_disabled_packs(disabled))


def resolve_conversation_selection(conv: Any, settings: Any) -> SkillSelection:
    disabled = getattr(conv, "disabled_packs", None)
    subs = getattr(conv, "enabled_subskills", None)
    if disabled is not None:
        return SkillSelection(
            disabled_packs=normalize_disabled_packs(list(disabled)),
            enabled_subskills=normalize_enabled_subskills(
                subs if isinstance(subs, dict) else None
            ),
        )
    # Legacy chats stored enabled_packs allowlists — treat as no deny (all available).
    base = default_selection_from_settings(settings)
    if isinstance(subs, dict) and subs:
        base.enabled_subskills = normalize_enabled_subskills(subs)
    return base


def normalize_enabled_skills(filenames: list[str] | None) -> list[str]:
    """Legacy: treat filenames as pack ids."""
    return normalize_enabled_packs(filenames)
