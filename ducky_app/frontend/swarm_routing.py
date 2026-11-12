"""Swarm routing: which template (ducky profile) and which model each job gets.

Jobs are decided when a swarm is created. Each job kind has its own model: the person's
pick in Settings (``swarm_job_models``), else the template profile's own model, else the
kind's suggested model when this PC can run it, else the lead's own model. Pure functions;
nothing here starts a chat.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

FALLBACK_KIND = "developer"
FALLBACK_PROFILE = "developer"


@dataclass(frozen=True)
class JobKind:
    kind: str
    keywords: tuple[str, ...]
    profile_id: str
    model: str
    """Suggested model for this kind; the person changes it per kind in Settings."""


JOB_KINDS: tuple[JobKind, ...] = (
    JobKind("narrative", ("narrative", "story", "dialogue", "quest", "lore", "character arc"), "developer",
            "claude-opus-5-5"),
    JobKind("research", ("research", "investigate", "survey", "compare", "benchmark"), "developer",
            "claude-sonnet-5-5"),
    JobKind("concept", ("concept", "art direction", "moodboard", "style guide", "art style"), "developer",
            "claude-sonnet-5-5"),
    JobKind("modeling", ("modeling", "modelling", "mesh", "blender", "sculpt", "prop", "3d"), "3d-modeler",
            "claude-sonnet-5-5"),
    JobKind("material", ("texture", "material", "shader"), "material-artist", "claude-sonnet-5-5"),
    JobKind("verse", ("verse", "gameplay", "device", "game logic", "logic"), "verse-coder", "claude-opus-5-5"),
    JobKind("ui", ("ui", "hud", "widget", "umg", "menu"), "ui-engineer", "claude-sonnet-5-5"),
    JobKind("level", ("level", "layout", "blockout", "map"), "level-designer", "claude-sonnet-5-5"),
    JobKind("npc", ("npc", "creature", "enemy", "enemies"), "npc-author", "claude-sonnet-5-5"),
    JobKind("vfx", ("vfx", "niagara", "particle", "effect"), "niagara-vfx", "claude-sonnet-5-5"),
    JobKind("sound", ("sound", "audio", "music", "sfx"), "sound-designer", "claude-sonnet-5-5"),
    JobKind("rig", ("rig", "rigging", "skeleton", "skin weights"), "rigging-artist", "claude-sonnet-5-5"),
    JobKind("animation", ("animation", "animate", "anim"), "animation-artist", "claude-sonnet-5-5"),
    JobKind("video", ("video", "trailer", "editing", "footage", "montage"), "developer", "claude-sonnet-5-5"),
    JobKind("writing", ("writing", "docs", "documentation", "readme", "patch notes", "copy"), "developer",
            "claude-sonnet-5-5"),
    JobKind("test", ("test", "qa", "playtest", "tester"), "tester", "claude-sonnet-5-5"),
    JobKind("review", ("review", "reviewer", "audit"), "reviewer", "claude-opus-5-5"),
    JobKind("app", ("app", "python", "backend", "frontend", "api", "sqlite", "database", "migration",
                    "typescript", "server", "script"), "developer", "claude-opus-5-5"),
)

_BY_KIND = {row.kind: row for row in JOB_KINDS}
_SUFFIX = r"(?:s|es|ing|ed)?"


def _pattern(keyword: str) -> re.Pattern[str]:
    words = r"\s+".join(re.escape(w) for w in keyword.split())
    return re.compile(rf"(?<![a-z0-9]){words}{_SUFFIX}(?![a-z0-9])")


_PATTERNS = {row.kind: tuple(_pattern(k) for k in row.keywords) for row in JOB_KINDS}


def job_kind(kind: str) -> JobKind | None:
    return _BY_KIND.get((kind or "").strip().lower())


def _match(text: str) -> tuple[JobKind | None, list[str]]:
    """The row with the most keyword hits (the job's name, before ':', counts double); ties go
    to the earlier row."""
    low = (text or "").lower()
    name = low.split(":", 1)[0] if ":" in low else ""
    best: JobKind | None = None
    best_score = 0
    best_hits: list[str] = []
    for row in JOB_KINDS:
        hits = [kw for kw, pat in zip(row.keywords, _PATTERNS[row.kind]) if pat.search(low)]
        if not hits:
            continue
        score = len(hits) + sum(1 for kw, pat in zip(row.keywords, _PATTERNS[row.kind]) if name and pat.search(name))
        if score > best_score:
            best, best_score, best_hits = row, score, hits
    return best, best_hits


def _profile_model(profile: Mapping[str, Any] | None) -> str:
    favs = (profile or {}).get("favorite_models") or []
    return next((str(m).strip() for m in favs if str(m).strip()), "") if isinstance(favs, list) else ""


def can_run(model: str, settings: Any, lead_provider: str = "") -> bool:
    """True when *model* is one this PC can run and an agent may seat: a signed-in coding
    agent or a provider offers it, and it is no API key closed to agents. A kind's suggested
    model is a Claude name; a person with only a Gemini key has nothing that runs it."""
    from frontend import favorite_models as fm

    try:
        result = fm.resolve_model_strict([model], settings)
    except Exception:
        return True  # unresolvable here: the spawn reports it
    if not isinstance(result, fm.ResolveOk):
        return False
    return isinstance(fm.refuse_paid_api_for_agent(result, settings, lead_provider), fm.ResolveOk)


def _agent_may_use(model: str, settings: Any, lead_provider: str = "") -> bool:
    """False when *model* bills a pay-per-token API key agents may not start chats on
    (``lead_provider``: the key the lead itself runs on, which its team may use)."""
    from frontend import favorite_models as fm

    selection = fm.parse_selection(model)
    if selection is not None:
        if selection.is_coding_agent:
            return True
        result: Any = fm.ResolveOk(coding_agent="ducky", model=selection.model_id,
                                   provider=selection.provider, selection=selection)
    else:
        try:
            result = fm.resolve_model_strict([model], settings)
        except Exception:
            return True  # unresolvable here: the spawn reports it
    refused = fm.refuse_paid_api_for_agent(result, settings, lead_provider)
    return not (isinstance(refused, fm.ResolveErr) and refused.code == "api_key_blocked")


def job_model(kind: str, profile: Mapping[str, Any] | None, settings: Any, lead_provider: str = "") -> str:
    """The model a job of *kind* runs on: Settings' ``swarm_job_models[kind]``, else the
    template profile's own model, else ''. Never a paid API model agents may not use."""
    picks = getattr(settings, "swarm_job_models", None) or {}
    key = (kind or "").strip().lower()
    for model in (str(picks.get(key) or "").strip() if isinstance(picks, Mapping) else "", _profile_model(profile)):
        if model:
            return model if _agent_may_use(model, settings, lead_provider) else ""
    return ""


def route(
    text: str,
    kind: str = "",
    *,
    settings: Any = None,
    profiles: Iterable[Mapping[str, Any]] | None = None,
    lead_model: str = "",
    lead_provider: str = "",
) -> dict[str, str]:
    """Route one job (its name and task) to {kind, profile_id, model, why}.

    An explicit *kind* wins, else keyword match on *text*, else 'developer'. A kind with no
    specialist profile runs on Developer and keeps its own name and model. *profiles* (the
    available ducky profiles) lets a missing specialist fall back to Developer; *settings*
    applies the person's model per kind. *lead_model* (the lead's own ``backend:model``) is
    what the job gets when its kind's suggested model can't run on this PC; *lead_provider*
    is the API key the lead runs on, which its team may use.
    """
    explicit = (kind or "").strip().lower()
    row = job_kind(explicit) if explicit else None
    if explicit:
        why = f"kind '{explicit}' was given"
        if row is None:
            why += " (no table row: Developer runs it)"
    else:
        row, hits = _match(text)
        why = (f"matched {', '.join(repr(h) for h in hits)}" if row
               else "no job kind matched: Developer runs it")
    out_kind = row.kind if row else (explicit or FALLBACK_KIND)
    profile_id = row.profile_id if row else FALLBACK_PROFILE
    by_id = {str(p.get("id") or ""): p for p in profiles} if profiles is not None else None
    if by_id is not None and profile_id not in by_id:
        why += f"; profile '{profile_id}' is not available, Developer runs it"
        profile_id = FALLBACK_PROFILE
    profile = by_id.get(profile_id) if by_id is not None else None
    if settings is not None:
        model = job_model(out_kind, profile, settings, lead_provider)
    else:
        model = _profile_model(profile)
    if not model:
        model = row.model if row else ""
        if lead_model and settings is not None and not (model and can_run(model, settings, lead_provider)):
            model = lead_model
    return {"kind": out_kind, "profile_id": profile_id, "model": model, "why": why}


# --------------------------------------------------------------------------- detail level
# The jobs always come from the person's request; the detail level only reshapes them:
# 1 merges each area into one job, 2 keeps them, 3 splits a kind into its parts, 4 also
# makes each split job a sub-group whose parts are its members.

MIN_DETAIL, MAX_DETAIL = 1, 4

# Other names people and leads use for a kind in JOB_KINDS.
KIND_ALIASES = {"texture": "material", "rigging": "rig", "gameplay": "verse", "tests": "test", "qa": "test"}

AREAS: dict[str, tuple[str, ...]] = {
    "Art": ("modeling", "texture", "rigging", "animation", "vfx"),
    "Code": ("verse", "ui", "gameplay"),
    "Story": ("narrative", "writing"),
    "World": ("level", "npc", "sound"),
    "Check": ("test", "review"),
}

PARTS: dict[str, tuple[str, ...]] = {
    "modeling": ("blockout", "hero props", "set dressing"),
    "texture": ("materials", "decals"),
    "verse": ("systems", "devices"),
    "ui": ("HUD", "menus"),
    "level": ("layout", "lighting"),
}


def canonical_kind(kind: str) -> str:
    key = (kind or "").strip().lower()
    return KIND_ALIASES.get(key, key)


def _canon_map(table: Mapping[str, tuple[str, ...]]) -> dict[str, str]:
    return {canonical_kind(k): area for area, kinds in table.items() for k in kinds}


_AREA_OF = _canon_map(AREAS)
_PARTS_OF = {canonical_kind(k): parts for k, parts in PARTS.items()}


def _job_kind_of(job: Mapping[str, Any]) -> str:
    kind = canonical_kind(str(job.get("kind") or ""))
    if kind:
        return kind
    return route(f"{job.get('name') or ''}: {job.get('task') or ''}")["kind"]


def area_of(kind: str) -> str:
    """The area ('Art', 'Code', …) a job kind belongs to, else ''."""
    return _AREA_OF.get(canonical_kind(kind), "")


def parts_of(kind: str) -> tuple[str, ...]:
    return _PARTS_OF.get(canonical_kind(kind), ())


def _paths(job: Mapping[str, Any]) -> list[str]:
    raw = job.get("paths") or []
    return [str(p) for p in raw] if isinstance(raw, (list, tuple)) else [str(raw)]


def _merge(jobs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """One '<Area>' job per area, in the order the areas first appear; jobs of no area stay."""
    out: list[dict[str, Any]] = []
    by_area: dict[str, dict[str, Any]] = {}
    for job in jobs:
        area = area_of(_job_kind_of(job))
        if not area:
            out.append(dict(job))
            continue
        merged = by_area.get(area)
        task = f"{job.get('name') or ''}: {job.get('task') or ''}".strip(": ")
        if merged is None:
            merged = {**job, "name": area, "task": task, "paths": _paths(job),
                      "kind": _job_kind_of(job), "kinds": [_job_kind_of(job)], "area": area}
            by_area[area] = merged
            out.append(merged)
            continue
        merged["task"] = "\n".join(t for t in (merged["task"], task) if t)
        merged["paths"] += [p for p in _paths(job) if p not in merged["paths"]]
        if _job_kind_of(job) not in merged["kinds"]:
            merged["kinds"].append(_job_kind_of(job))
    return out


def _split(jobs: list[dict[str, Any]], *, grouped: bool) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for job in jobs:
        parts = parts_of(_job_kind_of(job))
        if not parts:
            out.append(dict(job))
            continue
        name = str(job.get("name") or _job_kind_of(job).title())
        head = name.split(":", 1)[0].strip() or name
        task = str(job.get("task") or "")
        for part in parts:
            child = {**job, "name": f"{head}: {part}", "task": f"{part} — {task}" if task else part,
                     "paths": _paths(job), "part": part, "parent": name}
            if grouped:
                child["group"] = name
            out.append(child)
    return out


def split_jobs(jobs: Iterable[Mapping[str, Any]], detail: int) -> list[dict[str, Any]]:
    """Reshape the request's jobs for a detail level (clamped to 1–4).

    Each job is a dict with at least ``name`` (and ``task``, ``kind``, ``profile_id``,
    ``model``, ``paths`` when known). 1: jobs of one area merge into one '<Area>' job, tasks
    and paths joined. 2: as they are. 3: a job whose kind has PARTS becomes one job per part
    ('Modeling: blockout'), keeping the parent's kind, profile and model. 4: like 3, and each
    part also gets ``group`` = its parent's name so the parent becomes a sub-group. Kinds
    with no parts never split. The input is never changed.
    """
    items = [dict(j) for j in jobs]
    level = max(MIN_DETAIL, min(MAX_DETAIL, int(2 if detail is None else detail)))
    if level == 1:
        return _merge(items)
    if level == 2:
        return items
    return _split(items, grouped=level == 4)
