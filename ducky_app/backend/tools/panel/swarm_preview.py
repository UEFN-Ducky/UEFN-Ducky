"""Swarm preview: the questions, the tree and the preview ducky_swarm_create shows. Pure: builds nothing.

``build_preview`` returns the ``preview`` part of the swarm card payload
(``ui: "swarm_builder"``, pinned in web/src/swarm/fixtures/swarm_builder.json):

- ``goal``, ``detail`` (1-4), ``agents``, ``max_agents``, ``per_group``
- ``levels``: [{detail, name, agents, groups}] — one per detail level (the slider)
- ``tree``: groups first, then root seats. Group = {group, purpose, muted, seats, groups};
  seat = {name, job, kind, profile, model, paths, sub_plan: {title, done, total, steps}}
- ``muted``: names of muted groups; ``lanes``: {seat name: [paths]}
- ``plan``: {title, sections: [{seat, title, after, steps}]} — the plan Build makes; ``after`` (on a
  section or a step) lists refs "<seat name>#<n>" (that seat's n-th step, 1-based) to finish first
- ``oversized``: [{seat, step, id, outcomes, areas, chip, split: [steps]}] — steps too big for one turn;
  edit {seat, split_step: <content>} applies the split
- ``balance``: {waves: [seats at work per wave], peak, start_later: [{seat, step, id, wave, after}],
  split: [{seat, steps, proposal}], merge: [{seat, into}], summary}
- ``estimate``: {seats: {name: {tokens, minutes, cost_usd}}, total: {tokens, minutes, cost_usd, text}}
- ``jobs`` (server only, dropped before it reaches the card)

The card payload adds, around ``preview``: ``ui``, ``status``, ``goal``, ``seats`` (flat:
{name, group, profile, model, lane, steps, flags}) and ``estimate`` ({seats: {name: {minutes,
cost_usd}}, total: {minutes, cost_usd}}).
"""

from __future__ import annotations

import re
from typing import Any

SUGGESTED_MAX_AGENTS = 8
SUGGESTED_PER_GROUP = 5
MAX_DEPTH = 3  # root group → area group → split group
DONE_LINE = "Done when: the change and its tests pass; commit your files (git add <files>) and push."

# Job kind → (area, purpose) of the sub-group related jobs nest into. test/review stay at the top.
_AREAS: dict[str, tuple[str, str]] = {
    "modeling": ("Art", "assets"), "material": ("Art", "assets"), "rig": ("Art", "assets"),
    "animation": ("Art", "assets"), "concept": ("Art", "assets"), "vfx": ("Art", "assets"),
    "verse": ("Code", "gameplay"), "ui": ("Code", "gameplay"), "npc": ("Code", "gameplay"),
    "app": ("Code", "app"), "developer": ("Code", "app"),
    "level": ("World", "levels"), "sound": ("Audio", "sound"),
    "narrative": ("Story", "writing"), "writing": ("Story", "writing"),
    "research": ("Research", "findings"), "video": ("Media", "video"),
}
_TOP_KINDS = {"test": "Tester", "review": "Reviewer"}


# ---- jobs ---------------------------------------------------------------------------------

_CODE_EXT = (".py", ".ts", ".tsx", ".js", ".jsx", ".mjs", ".rs", ".go", ".cs", ".cpp", ".c", ".h", ".java")
# Kinds the keyword router picks from a job's words that can't be right when every path is code.
_PROSE_KINDS = {"writing", "narrative", "research", "concept", "video"}


def _is_code_path(path: str) -> bool:
    return path.strip().lower().endswith(_CODE_EXT)


def fix_routes(jobs: list[dict[str, Any]], lead_model: str = "") -> list[dict[str, Any]]:
    """Jobs whose paths are all code run as 'developer' (the words said 'docs' or 'safety'), and
    every job has a suggested model: its kind's, else the lead's, else the app kind's."""
    from frontend.swarm_routing import job_kind

    out = []
    for job in jobs:
        job = dict(job)
        if job.get("paths") and job.get("kind") in _PROSE_KINDS and all(_is_code_path(p) for p in job["paths"]):
            job.update(kind="developer", profile="developer", suggested_model="")
        if not job.get("suggested_model"):
            row = job_kind(job.get("kind") or "")
            job["suggested_model"] = (row.model if row else "") or lead_model or job_kind("app").model
        out.append(job)
    return out


def _steps(raw: Any) -> list[dict[str, str]]:
    out = []
    for s in raw or []:
        if isinstance(s, dict):
            content = str(s.get("content") or s.get("title") or "").strip()
            body = str(s.get("body_markdown") or s.get("body") or "").strip()
        else:
            content, body = str(s or "").strip(), ""
        if content:
            if "done when" not in body.lower():
                body = (body + "\n" if body else "") + DONE_LINE
            out.append({"content": content, "body_markdown": body})
    return out


DETAILS = {1: "Areas", 2: "Jobs", 3: "Parts", 4: "Parts in sub-groups"}
SUGGESTED_DETAIL = 2


def _slice_parts(out: list[dict[str, Any]], jobs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Parts of one job get disjoint slices of its paths, never the same lane: a job with one
    path (or none) stays one seat, and parts beyond the path count merge into the last part."""
    by_parent: dict[str, list[dict[str, Any]]] = {}
    for job in out:
        if job.get("part"):
            by_parent.setdefault(str(job["parent"]), []).append(job)
    result: list[dict[str, Any]] = []
    for job in out:
        parent = str(job.get("parent") or "")
        if not job.get("part"):
            result.append(job)
            continue
        parts = by_parent.pop(parent, None)
        if parts is None:
            continue  # already placed with its first sibling
        paths = list(parts[0].get("paths") or [])
        if len(paths) < 2:
            result.append(next(dict(j) for j in jobs if j["name"] == parent))
            continue
        n = min(len(parts), len(paths))
        kept = [dict(p) for p in parts[:n]]
        for extra in parts[n:]:
            label = f"{kept[-1]['part']} + {extra['part']}"
            kept[-1].update(part=label, name=f"{kept[-1]['name'].split(':', 1)[0]}: {label}")
        for i, part in enumerate(kept):
            part["paths"] = paths[i * len(paths) // n:(i + 1) * len(paths) // n]
        result += kept
    return result


def apply_detail(jobs: list[dict[str, Any]], detail: int) -> list[dict[str, Any]]:
    """swarm_routing.split_jobs for *detail*, with the steps each reshaped job really owns:
    an area job gets every merged job's steps, a part gets its parent's steps marked with the
    part (split_jobs copies only the first job's / the whole parent's)."""
    from frontend.swarm_routing import area_of, split_jobs

    out = _slice_parts(split_jobs(jobs, detail), jobs)
    for job in out:
        if job.get("part"):
            part = str(job["part"])
            # ponytail: each part runs every parent step for its slice; real per-part steps need the lead.
            job["steps"] = [{"content": f"{part}: {s['content']}", "body_markdown": s["body_markdown"]}
                            for s in job["steps"]] or _steps([job["name"]])
        elif "kinds" in job and job.get("area"):
            same = [j for j in jobs if area_of(j["kind"]) == job["area"]]
            job["steps"] = [s for j in same for s in j["steps"]]
            job["purpose"] = "; ".join(j["purpose"] for j in same if j["purpose"])
    return out


# ---- s4-ask: the questions ----------------------------------------------------------------

def _parse_agents(agents: Any) -> str | int:
    """'' (not answered), 'auto', or a positive int."""
    if isinstance(agents, bool):
        return ""
    if isinstance(agents, int):
        return agents if agents > 0 else ""
    text = str(agents or "").strip().lower()
    if text == "auto":
        return "auto"
    return int(text) if text.isdigit() and int(text) > 0 else ""


def detail_levels(jobs: list[dict[str, Any]], agents: Any, max_agents: int, per_group: int) -> list[dict[str, Any]]:
    """[{detail, agents, groups}] for every detail level; unanswered limits count as the suggested ones."""
    max_agents = max_agents if max_agents > 0 else SUGGESTED_MAX_AGENTS
    per_group = per_group if per_group > 0 else SUGGESTED_PER_GROUP
    out = []
    for d in DETAILS:
        tree = plan_tree(apply_detail(jobs, d), agents, max_agents, per_group)
        out.append({"detail": d, "name": DETAILS[d], "agents": len(list(walk_seats(tree))),
                    "groups": len(list(walk_groups(tree)))})
    return out


def distinct_levels(levels: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """One level per (agents, groups) shape; the suggested level wins its shape, else the first."""
    by_shape: dict[tuple[int, int], dict[str, Any]] = {}
    for lv in levels:
        shape = (lv["agents"], lv["groups"])
        if shape not in by_shape or lv["detail"] == SUGGESTED_DETAIL:
            by_shape[shape] = lv
    return sorted(by_shape.values(), key=lambda lv: lv["detail"])


def questions_for(jobs: list[dict[str, Any]], agents: Any, max_agents: int, per_group: int,
                  detail: int = SUGGESTED_DETAIL, lead_model: str = "") -> list[dict[str, Any]]:
    """The ducky_ask_user questions still unanswered (empty = go on)."""
    jobs = fix_routes(jobs, lead_model)
    qs: list[dict[str, Any]] = []
    if detail not in DETAILS:
        qs.append({
            "id": "detail",
            "prompt": "How finely should the work be split?",
            "options": [
                {"id": str(lv["detail"]),
                 "label": f"{lv['detail']} {lv['name']}" + (" (suggested)" if lv["detail"] == SUGGESTED_DETAIL else ""),
                 "description": f"{lv['agents']} agents" + (f" in {lv['groups']} sub-group{'s' if lv['groups'] > 1 else ''}" if lv["groups"] else "")}
                for lv in distinct_levels(detail_levels(jobs, agents, max_agents, per_group))
            ],
        })
    if _parse_agents(agents) == "":
        qs.append({
            "id": "agents",
            "prompt": f"How many agents should work on this? There are {len(jobs)} jobs.",
            "options": [
                {"id": "auto", "label": f"Auto ({len(jobs)})", "description": "One agent per job, up to the size limit."},
                {"id": "number", "label": "A number", "description": "Type how many agents; related jobs share one."},
            ],
        })
    if max_agents <= 0 or per_group <= 0:
        qs.append({
            "id": "size_limits",
            "prompt": (
                "Size limits: the most agents in the whole swarm, and the most agents directly in one "
                "group before Ducky splits them into sub-groups."
            ),
            "options": [
                {"id": f"{SUGGESTED_MAX_AGENTS}/{SUGGESTED_PER_GROUP}",
                 "label": f"{SUGGESTED_MAX_AGENTS} total · {SUGGESTED_PER_GROUP} per group (suggested)",
                 "description": ("Example: a game team of 8 becomes 'Art: assets' (Modeling, Texture) and "
                                 "'Code: gameplay' (Verse, UI) as sub-groups, with Narrative, Research, Tester "
                                 "and Reviewer directly under the lead.")},
                {"id": "other", "label": "Other limits", "description": "Type 'total / per group', e.g. 12 / 4."},
            ],
        })
    for i, job in enumerate(jobs):
        if job["model"]:
            continue
        suggested = job["suggested_model"]
        qs.append({
            "id": f"model:{i}",
            "prompt": f"Model for {job['name']} ({job['profile']})?",
            "options": [
                *([{"id": suggested, "label": f"{suggested} (suggested)",
                    "description": f"The {job['kind']} job's usual model."}] if suggested else []),
                {"id": "other", "label": "Another model", "description": "Type a model id, e.g. codex:gpt-5-mini."},
            ],
        })
    return qs


# ---- s4-split: size the team and the tree -------------------------------------------------

def _merge(into: dict[str, Any], job: dict[str, Any]) -> None:
    into["name"] = f"{into['name']} + {job['name']}"
    into["steps"] = into["steps"] + job["steps"]
    into["paths"] = into["paths"] + [p for p in job["paths"] if p not in into["paths"]]
    into["purpose"] = "; ".join(x for x in (into["purpose"], job["purpose"]) if x)
    into["task"] = "\n".join(x for x in (into["task"], job["task"]) if x)


def _cap(jobs: list[dict[str, Any]], cap: int) -> list[dict[str, Any]]:
    """Fit jobs into *cap* seats: the last job merges into an earlier one of its area, else
    into the job before it. Nothing is dropped."""
    jobs = [dict(j) for j in jobs]
    while len(jobs) > max(cap, 1):
        last = jobs.pop()
        area = _AREAS.get(last["kind"])
        same = [j for j in jobs if area and _AREAS.get(j["kind"]) == area]
        _merge(same[-1] if same else jobs[-1], last)
    return jobs


def capped_jobs(jobs: list[dict[str, Any]], agents: Any, max_agents: int) -> list[dict[str, Any]]:
    """The jobs as seated: 'auto' = one per job, a number = that many, both capped at max_agents."""
    want = _parse_agents(agents)
    cap = len(jobs) if want in ("", "auto") else int(want)
    return _cap(jobs, min(cap, max_agents) if max_agents > 0 else cap)


def _seat_name(job: dict[str, Any], taken: set[str]) -> str:
    part = str(job.get("part") or "").strip()
    head = part[:1].upper() + part[1:] if part else job["name"].split(":", 1)[0].split(" + ", 1)[0].strip()
    base = _TOP_KINDS.get(job["kind"]) or f"{head} lead"
    name, n = base, 2
    while name in taken:
        name, n = f"{base} {n}", n + 1
    taken.add(name)
    return name


def _seat(job: dict[str, Any], taken: set[str]) -> dict[str, Any]:
    return {
        "name": _seat_name(job, taken),
        "job": job["name"],
        "kind": job["kind"],
        "profile": job["profile"],
        "model": job["model"] or job["suggested_model"],
        "paths": job["paths"],
        "sub_plan": {"title": job["name"], "done": 0, "total": len(job["steps"]),
                     "steps": [dict(s) for s in job["steps"]]},
    }


def _group(name: str, purpose: str, seats: list[dict[str, Any]], per_group: int, depth: int) -> dict[str, Any]:
    """A group of seats; more than *per_group* seats split into numbered sub-groups."""
    node = {"group": f"{name}: {purpose}", "purpose": purpose, "muted": True, "seats": seats, "groups": []}
    if len(seats) > per_group and depth < MAX_DEPTH:
        chunks = [seats[i:i + per_group] for i in range(0, len(seats), per_group)]
        node["seats"] = []
        node["groups"] = [_group(f"{name} {i}", purpose, c, per_group, depth + 1) for i, c in enumerate(chunks, 1)]
    return node


def plan_tree(jobs: list[dict[str, Any]], agents: Any, max_agents: int, per_group: int) -> list[dict[str, Any]]:
    """Tree nodes (groups first, then root seats) for the answered size limits.

    'auto' = one agent per job; a number = that many; both capped at *max_agents* (jobs merge,
    never drop). The root keeps at most *per_group* seats: related jobs nest into
    '<Area>: <purpose>' sub-groups, biggest area first; Tester and Reviewer stay at the root.
    """
    per_group = max(per_group, 1)
    taken: set[str] = set()
    seated = [(job, _seat(job, taken)) for job in capped_jobs(jobs, agents, max_agents)]
    # Detail 4: the parts of one job (split_jobs tags them with group = the parent) form its sub-group.
    parts: dict[str, list[dict[str, Any]]] = {}
    for job, seat in seated:
        if job.get("group"):
            parts.setdefault(str(job["group"]).split(":", 1)[0].strip(), []).append(seat)
    groups: list[dict[str, Any]] = [_group(parent, "parts", seats, per_group, 2) for parent, seats in parts.items()]
    in_parts = [s for seats in parts.values() for s in seats]
    seated = [(j, s) for j, s in seated if s not in in_parts]
    top = [s for _j, s in seated]
    if len(top) <= per_group:
        return [*groups, *top]
    areas: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for job, seat in seated:
        if job["kind"] not in _TOP_KINDS:
            areas.setdefault(_AREAS.get(job["kind"], ("Team", "jobs")), []).append(seat)
    for (area, purpose), seats in sorted(areas.items(), key=lambda kv: -len(kv[1])):
        if len(top) <= per_group or len(seats) < 2:
            break
        groups.append(_group(area, purpose, seats, per_group, 2))
        top = [s for s in top if s not in seats]
    loose = [s for s in top if s["kind"] not in _TOP_KINDS]
    if len(top) > per_group and len(loose) > 1:
        # ponytail: one catch-all group when no area has two jobs left; smarter pairing if it shows up.
        groups.append(_group("Team", "more jobs", loose, per_group, 2))
        top = [s for s in top if s not in loose]
    return [*groups, *top]


def walk_groups(nodes: list[dict[str, Any]]):
    for node in nodes:
        if "group" in node:
            yield node
            yield from walk_groups(node["groups"])


def walk_seats(nodes: list[dict[str, Any]]):
    for node in nodes:
        if "group" in node:
            yield from walk_seats(node["seats"])
            yield from walk_seats(node["groups"])
        else:
            yield node


# ---- seats as a step graph (job previews and real plans) ----------------------------------
# A graph seat = {name, id, lane, after: [refs], steps: [{id, content, body, after: [refs]}]}.
# A ref names a step id, or a seat/section id meaning all of its steps. In a job preview the
# step ids are "<seat name>#<n>" (1-based) and a seat's id is its name.

def preview_seats(tree: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for s in walk_seats(tree):
        sub = s["sub_plan"]
        out.append({"name": s["name"], "id": s["name"], "lane": list(s["paths"]), "model": s["model"],
                    "after": list(sub.get("after") or []),
                    "steps": [{"id": f"{s['name']}#{i}", "content": st["content"],
                               "body": st.get("body_markdown") or "", "after": list(st.get("after") or [])}
                              for i, st in enumerate(sub["steps"], 1)]})
    return out


def step_deps(seats: list[dict[str, Any]], expand: dict[str, list[str]] | None = None) -> dict[str, list[str]]:
    """{step id: the step ids it waits for}, from step and seat ``after`` refs. *expand* maps
    other ref ids (a plan's group sections) to their step ids."""
    refs: dict[str, list[str]] = dict(expand or {})
    for seat in seats:
        ids = [st["id"] for st in seat["steps"]]
        refs[seat["id"]] = ids
        refs.update({i: [i] for i in ids})
    deps: dict[str, list[str]] = {}
    for seat in seats:
        own = {st["id"] for st in seat["steps"]}
        seat_wide = [d for r in seat["after"] for d in refs.get(r, []) if d not in own]
        for st in seat["steps"]:
            mine = [d for r in st["after"] for d in refs.get(r, []) if d != st["id"]]
            deps[st["id"]] = list(dict.fromkeys(seat_wide + mine))
    return deps


def _finish_steps(seats: list[dict[str, Any]], deps: dict[str, list[str]]) -> dict[str, int]:
    """{step id: steps of work done when it finishes}: a seat works its steps in order and a
    step starts once everything it waits for is done. A cycle counts as not waiting."""
    prev = {st["id"]: (seat["steps"][i - 1]["id"] if i else None)
            for seat in seats for i, st in enumerate(seat["steps"])}
    done: dict[str, int] = {}
    busy: set[str] = set()

    def finish(sid: str) -> int:
        if sid in done:
            return done[sid]
        if sid in busy:
            return 0
        busy.add(sid)
        waits = [finish(d) for d in deps.get(sid, [])] + ([finish(prev[sid])] if prev.get(sid) else [])
        busy.discard(sid)
        done[sid] = max(waits, default=0) + 1
        return done[sid]

    for sid in prev:
        finish(sid)
    return done


# ---- cost and time --------------------------------------------------------------------------

TOKENS_PER_STEP = 150_000  # ponytail: one baseline for every step; tune it from real swarm runs
MINUTES_PER_STEP = 10
OUTPUT_SHARE = 0.05  # the rest is input, mostly cache reads in an agent loop
_AGENT_PREFIXES = ("claude", "codex", "ducky", "cursor")


def _price(model: str) -> tuple[float, float, float, float] | None:
    """(input, cached input, output, cache write) USD per 1M tokens, None when unknown."""
    head, sep, rest = model.partition(":")
    mid = rest if sep and head.lower() in _AGENT_PREFIXES else model
    try:
        from backend.agent.model_pricing import infer_provider, price_for_model

        return price_for_model(mid, infer_provider(mid))
    except Exception:
        return None


def estimate(seats: list[dict[str, Any]]) -> dict[str, Any]:
    """{seats: {name: {tokens, minutes, cost_usd}}, total: {tokens, minutes, cost_usd, text}}.
    Seat minutes = its own steps; total minutes = the longest chain through ``after``."""
    out: dict[str, Any] = {}
    for seat in seats:
        tokens = len(seat["steps"]) * TOKENS_PER_STEP
        price = _price(str(seat.get("model") or ""))
        cost = (None if price is None else
                round((tokens * (1 - OUTPUT_SHARE) * price[1] + tokens * OUTPUT_SHARE * price[2]) / 1e6, 2))
        out[seat["name"]] = {"tokens": tokens, "minutes": len(seat["steps"]) * MINUTES_PER_STEP, "cost_usd": cost}
    tokens = sum(v["tokens"] for v in out.values())
    minutes = max(_finish_steps(seats, step_deps(seats)).values(), default=0) * MINUTES_PER_STEP
    costs = [v["cost_usd"] for v in out.values() if v["cost_usd"] is not None]
    cost = round(sum(costs), 2) if costs else None
    text = f"~{tokens / 1e6:.1f}M tokens · ~{minutes} min" + (f" · ~${cost:.2f}" if cost is not None else "")
    return {"seats": out, "total": {"tokens": tokens, "minutes": minutes, "cost_usd": cost, "text": text}}


# ---- interfaces first, glue owned by the Integrator ---------------------------------------

CODE_KINDS = {"developer", "app"}
INTEGRATOR = "Integrator"
# The files every seat of a plugin or app feature would touch; the Integrator alone owns them.
GLUE_FILES = ("plugin.json", "backend/__init__.py", "ui/index.html")
_INTERFACES_STEP = {
    "content": "Interfaces: pin the shared names before anyone builds",
    "body_markdown": ("Write and commit what the seats call across modules: RPC and panel method names, "
                      "error codes, helper signatures, as stubs with tests against fakes. Every other seat "
                      "starts when this step is done."),
}
_WIRE_STEP = {
    "content": "Wire the seats' work into the glue files",
    "body_markdown": ("Register every seat's work: plugin.json inputs and tools, backend/__init__.py "
                      "register(), ui/index.html script tags and the panel-call list. Keep the shared lint "
                      "rules (relative imports) passing."),
}


def lane_match(pattern: str, path: str) -> bool:
    """*pattern* (a lane glob) covers *path*; a pattern lanes refuse covers nothing."""
    from backend.workspace import lanes

    try:
        return lanes.match(pattern, path)
    except ValueError:
        return False


def _literal_dir(path: str) -> list[str]:
    """Folder segments of a lane entry before its first wildcard (a file name dropped)."""
    segs: list[str] = []
    for seg in path.replace("\\", "/").strip("/").split("/"):
        if any(c in seg for c in "*?["):
            return segs
        segs.append(seg)
    return segs[:-1] if segs and "." in segs[-1] else segs


def _glue_root(paths: list[str]) -> str:
    """The deepest folder all *paths* share, above any backend/ or ui/ folder."""
    dirs = [_literal_dir(p) for p in paths] or [[]]
    root: list[str] = []
    for segs in zip(*dirs):
        if len(set(segs)) > 1:
            break
        root.append(segs[0])
    while root and root[-1] in ("backend", "ui"):
        root.pop()
    return "/".join(root)


def _carve(prefix: str, glue: set[str], files: list[str]) -> list[str]:
    """Lane entries covering every file under *prefix* except *glue* (from the repo's files)."""
    base = f"{prefix}/" if prefix else ""
    kids: dict[str, bool] = {}
    for f in files:
        if f.startswith(base):
            head, sep, _ = f[len(base):].partition("/")
            kids[head] = kids.get(head, False) or bool(sep)
    out: list[str] = []
    for name, is_dir in sorted(kids.items()):
        path = base + name
        if not is_dir:
            out += [] if path in glue else [path]
        elif any(g.startswith(path + "/") for g in glue):
            out += _carve(path, glue, files)
        else:
            out.append(f"{path}/**")
    return out


def _without_glue(lane: list[str], glue: list[str], repo_files: list[str] | None) -> list[str]:
    """*lane* minus the glue files: a folder lane holding one is carved into its other entries
    (needs *repo_files*); any other pattern stays and the owner check reports it."""
    out: list[str] = []
    for p in lane:
        if p in glue:
            continue
        held = {g for g in glue if lane_match(p, g)}
        dir_lane = p.endswith("/**") and not any(c in p[:-3] for c in "*?[")
        if held and dir_lane and repo_files is not None:
            out += [x for x in _carve(p[:-3], held, repo_files) if x not in out]
        else:
            out.append(p)
    return out


def add_integrator(tree: list[dict[str, Any]], lead_model: str = "",
                   repo_files: list[str] | None = None) -> dict[str, Any] | None:
    """3+ code seats share a codebase: add an Integrator seat at the root. Its first step
    ('Interfaces') runs before every other seat; its lane is the glue files, which leave
    every other lane; its last step wires the seats' work in after they finish. Returns its
    job, None when not needed."""
    seats = list(walk_seats(tree))
    code = [s for s in seats if s["kind"] in CODE_KINDS]
    if len(code) < 3 or any(s["name"] == INTEGRATOR for s in seats):
        return None
    root = _glue_root([p for s in code for p in s["paths"]])
    glue = [f"{root}/{g}" if root else g for g in GLUE_FILES]
    steps = _steps([_INTERFACES_STEP, _WIRE_STEP])
    steps[1]["after"] = [f"{s['name']}#{len(s['sub_plan']['steps'])}" for s in seats]
    for s in seats:
        s["sub_plan"]["after"] = [f"{INTEGRATOR}#1"]
        s["paths"] = _without_glue(s["paths"], glue, repo_files)
    model = lead_model or code[0]["model"]
    tree.append({"name": INTEGRATOR, "job": INTEGRATOR, "kind": "developer", "profile": "developer",
                 "model": model, "paths": glue,
                 "sub_plan": {"title": "Interfaces", "done": 0, "total": len(steps), "steps": steps}})
    return {"name": INTEGRATOR, "purpose": "Shared interfaces and the glue files every seat plugs into",
            "task": "", "steps": steps, "paths": glue, "kind": "developer", "profile": "developer",
            "suggested_model": model, "model": ""}


# ---- oversized steps ----------------------------------------------------------------------

MAX_OUTCOMES = 3  # more outcomes than this in one step = too big
MAX_AREAS = 2  # more file folders than this in one step = too big
_FILES_LINE = re.compile(r"^\s*\**files\**\s*:\**\s*(.+)$", re.IGNORECASE | re.MULTILINE)
_OUTCOME_SEP = re.compile(r",\s*(?:and\s+)?|\s+and\s+|\s*[&+]\s*")


def files_in(body: str) -> list[str]:
    """The paths on a body's 'Files:' line: its `backticked` names (parenthesised notes
    skipped), else its comma-separated words."""
    m = _FILES_LINE.search(body or "")
    if not m:
        return []
    text = re.sub(r"\([^)]*\)", "", m.group(1))
    ticks = re.findall(r"`([^`]+)`", text)
    items = [t.strip() for t in ticks] if ticks else [p.strip(" .*") for p in re.split(r",|;|\band\b", text)]
    return [p for p in items if p]


def outcomes(content: str) -> list[str]:
    """'Store: upload, events, deploy and install' → one 'Store: <x>' per listed outcome."""
    head, sep, rest = content.partition(":")
    if not sep:
        head, rest = "", content
    parts = [p.strip() for p in _OUTCOME_SEP.split(rest) if p.strip()]
    return [f"{head.strip()}: {p}" if head.strip() else p for p in parts] or [content]


def oversized_steps(seats: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Graph steps with more than MAX_OUTCOMES outcomes or MAX_AREAS file folders, each with
    the card's chip text and a split proposal (one step per outcome, else per folder)."""
    out = []
    for seat in seats:
        for st in seat["steps"]:
            parts = outcomes(st["content"])
            areas = sorted({"/".join(_literal_dir(p)) for p in files_in(st["body"])})
            if len(parts) <= MAX_OUTCOMES and len(areas) <= MAX_AREAS:
                continue
            split = parts if len(parts) > 1 else [f"{st['content']}: {a or 'top level'}" for a in areas]
            why = f"{len(parts)} outcomes" if len(parts) > MAX_OUTCOMES else f"{len(areas)} file areas"
            out.append({"seat": seat["name"], "step": st["content"], "id": st["id"], "outcomes": len(parts),
                        "areas": len(areas), "chip": f"Too big: {why} · Split",
                        "split": [{"content": c, "body_markdown": st["body"]} for c in split]})
    return out


def _split_edits(tree: list[dict[str, Any]], edits: list[dict[str, Any]]) -> None:
    """{seat, split_step: content} replaces that step with its split proposal (the card's Split)."""
    for e in edits:
        if "split_step" not in e:
            continue
        seat = next((s for s in walk_seats(tree) if s["name"] == str(e.get("seat") or "").strip()), None)
        steps = seat["sub_plan"]["steps"] if seat else []
        i = next((n for n, s in enumerate(steps) if s["content"] == str(e["split_step"]).strip()), None)
        if i is None:
            raise ValueError(f"No step {e['split_step']!r} on seat {e.get('seat')!r} to split")
        body = steps[i]["body_markdown"]
        steps[i:i + 1] = [{"content": c, "body_markdown": body} for c in outcomes(steps[i]["content"])]
        seat["sub_plan"]["total"] = len(steps)


# ---- preview and edits --------------------------------------------------------------------

def _job_edits(jobs: list[dict[str, Any]], edits: list[dict[str, Any]]) -> list[dict[str, Any]]:
    jobs = [dict(j) for j in jobs]
    for e in edits:
        if "drop" in e:
            jobs = [j for j in jobs if j["name"] != str(e["drop"]).strip()]
        elif "rename" in e:
            for j in jobs:
                if j["name"] == str(e["rename"]).strip():
                    j["name"] = str(e.get("to") or "").strip() or j["name"]
    return jobs


def _tree_edits(tree: list[dict[str, Any]], edits: list[dict[str, Any]]) -> None:
    groups = {g["group"]: g for g in walk_groups(tree)}
    seats = {s["name"]: s for s in walk_seats(tree)}
    for e in edits:
        seat = seats.get(str(e.get("seat") or "").strip())
        if "seat" in e and seat is None:
            raise ValueError(f"No seat called {e['seat']!r}; seats: {', '.join(seats)}")
        if seat is not None and any(k in e for k in ("add_step", "drop_step", "rename_step")):
            steps = seat["sub_plan"]["steps"]
            if "add_step" in e:
                steps.extend(_steps([e["add_step"]]))
            else:
                key = str(e.get("drop_step") or e.get("rename_step") or "").strip()
                hit = next((s for s in steps if s["content"] == key), None)
                if hit is None:
                    raise ValueError(f"{seat['name']} has no step {key!r}; steps: "
                                     + "; ".join(s["content"] for s in steps))
                if "drop_step" in e:
                    if len(steps) == 1:
                        raise ValueError(f"{seat['name']}'s sub plan needs at least one step: drop the job instead.")
                    steps.remove(hit)
                else:
                    hit["content"] = str(e.get("to") or "").strip() or hit["content"]
            seat["sub_plan"]["total"] = len(steps)
        elif seat is not None and e.get("model"):
            seat["model"] = str(e["model"]).strip()
        elif "mute" in e or "unmute" in e or ("group" in e and "muted" in e):
            key = str(e.get("mute") or e.get("unmute") or e.get("group")).strip()
            if key not in groups:
                raise ValueError(f"No group called {key!r}; groups: {', '.join(groups) or 'none'}")
            groups[key]["muted"] = bool(e["muted"]) if "muted" in e else "mute" in e
        elif "rename" in e and str(e["rename"]).strip() in groups:
            groups[str(e["rename"]).strip()]["group"] = str(e.get("to") or "").strip() or e["rename"]


def build_preview(goal: str, jobs: list[dict[str, Any]], agents: Any, max_agents: int, per_group: int,
                  edits: list[dict[str, Any]] | None = None, detail: int = SUGGESTED_DETAIL,
                  lead_model: str = "", repo_files: list[str] | None = None,
                  pc_limit: int | None = None) -> dict[str, Any]:
    """The preview for *jobs*; *repo_files* (the repo's file list) lets lanes be carved exactly."""
    edits = [e for e in edits or [] if isinstance(e, dict)]
    jobs = fix_routes(jobs, lead_model)
    kept = capped_jobs(_job_edits(apply_detail(jobs, detail), edits), agents, max_agents)
    tree = plan_tree(kept, agents, max_agents, per_group)
    _tree_edits(tree, edits)
    _split_edits(tree, edits)
    if all(e.get("interfaces", True) is not False for e in edits):
        made = add_integrator(tree, lead_model, repo_files)
        if made:
            kept.append(made)
    seats = list(walk_seats(tree))
    graph = preview_seats(tree)
    return {
        "goal": goal,
        "detail": detail,
        "levels": detail_levels(jobs, agents, max_agents, per_group),
        "agents": len(seats),
        "max_agents": max_agents,
        "per_group": per_group,
        "tree": tree,
        "muted": [g["group"] for g in walk_groups(tree) if g["muted"]],
        "lanes": {s["name"]: s["paths"] for s in seats},
        # The plan Build makes, as one outline: the person approves it with the tree.
        "plan": {"title": goal, "sections": [{"seat": s["name"], "title": s["sub_plan"]["title"],
                                               "after": list(s["sub_plan"].get("after") or []),
                                               "steps": s["sub_plan"]["steps"]} for s in seats]},
        "estimate": estimate(graph),
        "oversized": oversized_steps(graph),
        "balance": balance(graph, step_deps(graph), pc_limit=pc_limit),
        "jobs": {s["name"]: next(j for j in kept if j["name"] == s["job"]) for s in seats},
    }


# ---- checks on a real plan (the from-plan preview) ----------------------------------------

_HANDS_OVER = re.compile(r"^\s*\**hands over\**\s*:\**\s*(.+?)\s*(?:→|->)\s*(.+?)\s*$", re.IGNORECASE | re.MULTILINE)


def _kids(node: dict[str, Any]) -> list[dict[str, Any]]:
    return [c for c in node.get("children") or [] if isinstance(c, dict)]


def _leaf_ids(node: dict[str, Any]) -> list[str]:
    kids = _kids(node)
    return [i for c in kids for i in _leaf_ids(c)] if kids else [str(node.get("id") or "")]


def _ids(raw: Any) -> list[str]:
    return [str(a) for a in raw or [] if str(a).strip()]


def plan_seats(plan: dict[str, Any] | None) -> tuple[list[dict[str, Any]], dict[str, list[str]]]:
    """(graph seats, {section id: its step ids}) for a real plan. A section whose children are
    all steps is one seat, its lane the section's Files line; a group section's ``after``
    applies to every seat inside it."""
    seats: list[dict[str, Any]] = []
    expand: dict[str, list[str]] = {}

    def walk(nodes: list[dict[str, Any]], inherited: list[str]) -> None:
        for n in nodes:
            kids = _kids(n)
            if not kids:
                continue
            nid = str(n.get("id") or "")
            expand[nid] = _leaf_ids(n)
            after = inherited + _ids(n.get("after"))
            if any(_kids(k) for k in kids):
                walk(kids, after)
                continue
            body = str(n.get("body_markdown") or "")
            seats.append({"name": str(n.get("content") or nid), "id": nid, "lane": files_in(body), "body": body,
                          "model": str(n.get("model") or ""), "after": after,
                          "steps": [{"id": str(k.get("id") or ""), "content": str(k.get("content") or ""),
                                     "body": str(k.get("body_markdown") or ""), "after": _ids(k.get("after"))}
                                    for k in kids]})

    walk([n for n in (plan or {}).get("nodes") or [] if isinstance(n, dict)], [])
    return seats, expand


def _covers(pattern: str, path: str) -> bool:
    """A lane or Files entry names *path*: the same path, a glob over it, or its bare file name."""
    return (pattern == path or lane_match(pattern, path) or path.endswith("/" + pattern)
            or pattern.endswith("/" + path))


def _shared_files(a: list[str], b: list[str], repo_files: list[str] | None) -> list[str]:
    """Files both lanes hold: from the repo's file list, else the narrower of two overlapping entries."""
    if repo_files is not None:
        return [f for f in repo_files if any(_covers(p, f) for p in a) and any(_covers(q, f) for q in b)]
    out: list[str] = []
    for p in a:
        for q in b:
            hit = q if _covers(p, q) else p if _covers(q, p) else ""
            if hit and hit not in out:
                out.append(hit)
    return out


def _ancestors(deps: dict[str, list[str]]) -> dict[str, set[str]]:
    """{step id: every step it waits for, directly or through others}."""
    memo: dict[str, set[str]] = {}

    def up(sid: str, seen: frozenset[str]) -> set[str]:
        if sid in memo:
            return memo[sid]
        got: set[str] = set()
        for d in deps.get(sid, []):
            if d not in seen:
                got |= {d} | up(d, seen | {d})
        memo[sid] = got
        return got

    return {sid: up(sid, frozenset({sid})) for sid in deps}


def _handed_over(giver: dict[str, Any], taker: dict[str, Any], file: str, ancestors: dict[str, set[str]]) -> bool:
    """*giver* declares 'Hands over: <paths> → <taker>' for *file* and every taker step waits
    for the giver's last step."""
    if not giver["steps"] or not taker["steps"]:
        return False
    last = giver["steps"][-1]["id"]
    if not all(last in ancestors.get(st["id"], set()) for st in taker["steps"]):
        return False
    for paths, to in _HANDS_OVER.findall(giver["body"]):
        if to.strip(" `*.") in (taker["name"], taker["id"]) and any(_covers(p, file) for p in files_in(f"Files: {paths}")):
            return True
    return False


def owner_conflicts(seats: list[dict[str, Any]], deps: dict[str, list[str]],
                    repo_files: list[str] | None = None) -> list[dict[str, Any]]:
    """[{file, seats: [a, b]}] for every file two seats' lanes hold, unless it is a declared hand-over."""
    ancestors = _ancestors(deps)
    out = []
    for i, a in enumerate(seats):
        for b in seats[i + 1:]:
            for f in _shared_files(a["lane"], b["lane"], repo_files):
                if not (_handed_over(a, b, f, ancestors) or _handed_over(b, a, f, ancestors)):
                    out.append({"file": f, "seats": [a["name"], b["name"]]})
    return out


def after_cycles(seats: list[dict[str, Any]], deps: dict[str, list[str]]) -> list[list[str]]:
    """Each ``after`` loop once, as the step names around it."""
    names = {st["id"]: st["content"] for s in seats for st in s["steps"]}
    state: dict[str, int] = {}
    stack: list[str] = []
    seen: set[frozenset[str]] = set()
    out: list[list[str]] = []

    def visit(sid: str) -> None:
        state[sid] = 1
        stack.append(sid)
        for d in deps.get(sid, []):
            if state.get(d) == 1:
                loop = stack[stack.index(d):]
                if frozenset(loop) not in seen:
                    seen.add(frozenset(loop))
                    out.append([names.get(x, x) for x in loop])
            elif not state.get(d):
                visit(d)
        stack.pop()
        state[sid] = 2

    for sid in deps:
        if not state.get(sid):
            visit(sid)
    return out


def out_of_lane(seats: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """[{seat, step, file}] for a step whose own Files line names a path outside its seat's lane
    (a relative path is also read against a full-path lane's folder: ``in_lane_root``)."""
    from backend.agent.coding_agents.plan_review import in_lane_root

    return [{"seat": s["name"], "step": st["content"], "file": p}
            for s in seats if s["lane"] for st in s["steps"] for p in files_in(st["body"])
            if not any(_covers(q, p) or (in_lane_root(p, q) and _covers(q, in_lane_root(p, q))) for q in s["lane"])]


# ---- balance: most seats working, fewest waiting ------------------------------------------

SPLIT_FACTOR = 2  # a seat with more than this times the median step count is the slow tail


def _live_pc_limit() -> int:
    """K3's cap on agents running at once on this PC; 0 when it can't be read."""
    try:
        from frontend.ui_web import seat_queue

        return int(seat_queue.limit())
    except Exception:
        return 0


def _split_proposal(seat: dict[str, Any]) -> list[dict[str, Any]]:
    """The seat's steps grouped by the folder of each step's first file, else in two halves."""
    groups: dict[str, list[str]] = {}
    for st in seat["steps"]:
        files = files_in(st["body"])
        groups.setdefault("/".join(_literal_dir(files[0])) if files else "", []).append(st["content"])
    if len(groups) > 1:
        return [{"title": f"{seat['name']}: {k or 'other'}", "steps": v} for k, v in groups.items()]
    steps = [st["content"] for st in seat["steps"]]
    half = (len(steps) + 1) // 2
    return [{"title": f"{seat['name']} 1", "steps": steps[:half]}, {"title": f"{seat['name']} 2", "steps": steps[half:]}]


def balance(seats: list[dict[str, Any]], deps: dict[str, list[str]], conflicts: list[dict[str, Any]] = (),
            pc_limit: int | None = None) -> dict[str, Any]:
    """Waves (a step's wave = 1 + the latest wave it waits for), seats at work per wave, seats
    to start later (first step in wave 2+), slow-tail seats to split, one-step waiting seats
    to merge into what they wait for, and the suggested peak (capped by the PC limit)."""
    wave: dict[str, int] = {}

    def w(sid: str, seen: frozenset[str]) -> int:
        if sid not in wave:
            wave[sid] = 1 + max((w(d, seen | {sid}) for d in deps.get(sid, []) if d not in seen), default=0)
        return wave[sid]

    owner = {st["id"]: s["name"] for s in seats for st in s["steps"]}
    for sid in owner:
        w(sid, frozenset())
    count = max(wave.values(), default=0)
    widths = [len({owner[sid] for sid, k in wave.items() if k == n and sid in owner}) for n in range(1, count + 1)]
    later = []
    for s in seats:
        if not s["steps"]:
            continue
        first = min(s["steps"], key=lambda st: wave[st["id"]])
        if wave[first["id"]] >= 2:
            waits = list(dict.fromkeys(owner[d] for d in deps.get(first["id"], []) if d in owner))
            later.append({"seat": s["name"], "step": first["content"], "id": first["id"],
                          "wave": wave[first["id"]], "after": waits})
    sizes = sorted(len(s["steps"]) for s in seats)
    median = sizes[len(sizes) // 2] if sizes else 0
    split = [{"seat": s["name"], "steps": len(s["steps"]), "proposal": _split_proposal(s)}
             for s in seats if median and len(s["steps"]) > SPLIT_FACTOR * median]
    clashing = {name for c in conflicts for name in c["seats"]}
    merge = [{"seat": x["seat"], "into": x["after"][0]} for x in later
             if x["after"] and x["seat"] not in clashing and x["after"][0] not in clashing
             and len(next(s for s in seats if s["name"] == x["seat"])["steps"]) == 1]
    limit = _live_pc_limit() if pc_limit is None else pc_limit
    peak = min(max(widths, default=0), limit) if limit else max(widths, default=0)
    summary = " · ".join([f"Peak {peak} agents", f"waves {' / '.join(map(str, widths))}",
                          f"{len(later)} seat{'s' if len(later) != 1 else ''} start later",
                          *[f"split {x['seat']} ({x['steps']} steps)" for x in split]])
    return {"waves": widths, "peak": peak, "start_later": later, "split": split, "merge": merge,
            "summary": summary}


def check_plan(plan: dict[str, Any] | None, repo_files: list[str] | None = None,
               pc_limit: int | None = None) -> dict[str, Any]:
    """The from-plan preview's checks. ``status`` is 'needs_answers' (build nothing) while two
    seats own a file without a hand-over, ``after`` loops, or a step names a file outside its lane."""
    seats, expand = plan_seats(plan)
    deps = step_deps(seats, expand)
    found = {"conflicts": owner_conflicts(seats, deps, repo_files), "cycles": after_cycles(seats, deps),
             "out_of_lane": out_of_lane(seats)}
    return {"status": "needs_answers" if any(found.values()) else "ok", **found,
            "oversized": oversized_steps(seats), "estimate": estimate(seats),
            "balance": balance(seats, deps, found["conflicts"], pc_limit)}


# ---- the plan's repo ----------------------------------------------------------------------

_REPO_LINE = re.compile(r"^\s*(?:[-*]\s*)?(?:\*\*)?Repo(?:\*\*)?\s*:(?:\*\*)?\s*(.+)$", re.IGNORECASE | re.MULTILINE)


def _plan_texts(plan: dict[str, Any]) -> list[str]:
    out = [str(plan.get(k) or "") for k in ("overview", "body_markdown", "description")]

    def walk(nodes: Any) -> None:
        for n in nodes or []:
            if isinstance(n, dict):
                out.append(str(n.get("body_markdown") or ""))
                walk(n.get("children"))

    walk(plan.get("nodes"))
    return out


def plan_repo(plan: dict[str, Any] | None, roots: list[str] | None = None) -> str:
    """The repo a plan's relative Files mean: its ``Repo:`` line (a full path inside one of the
    person's Ducky projects), '' when it names none. Nov 5 2026: a plan about UEFN-Ducky-Release
    built while LimoRide was open put every member's ``ducky_app/...`` lane inside the island."""
    if not plan:
        return ""
    if roots is None:
        from backend.workspace.lanes import project_roots

        roots = project_roots()
    known = [r.replace("\\", "/").rstrip("/") for r in roots if r]
    for text in _plan_texts(plan):
        for m in _REPO_LINE.finditer(text):
            line = m.group(1)
            for raw in [*re.findall(r"`([^`]+)`", line), line]:
                p = raw.strip().strip("`*. ").replace("\\", "/").rstrip("/")
                if not p:
                    continue
                for r in sorted(known, key=len, reverse=True):
                    if p.lower() == r.lower() or p.lower().startswith(r.lower() + "/"):
                        return r + p[len(r):]
    return ""


# ---- lanes from the exact Files lines -----------------------------------------------------

# A Files entry that reads as a path or glob (a slash, a '*', or a file extension).
_PATH_LIKE = re.compile(r"[/\\*]|\.[A-Za-z][A-Za-z0-9]{0,4}$")


def _norm(path: str) -> str:
    return path.strip().strip("`").replace("\\", "/").removeprefix("./")


def resolve_path(path: str, repo_files: list[str] | None) -> tuple[str, str]:
    """(*path* as the repo names it, warning). A short path (``terminal/x.py``, ``agent_host/**``)
    becomes the one repo path ending with it; several or none keep it as written, with a
    warning. Full paths, and every path when *repo_files* is None, stay as they are.
    Nov 5 2026: auto lanes dropped the ``ducky_app/`` prefix from short test paths."""
    p = _norm(path)
    if repo_files is None or not p or re.match(r"^(?:[A-Za-z]:)?/", p):
        return p, ""
    head, star, _ = p.partition("*")
    literal = head if star else p
    if star and not literal.endswith("/"):
        literal = literal.rsplit("/", 1)[0] + "/" if "/" in literal else ""
    literal = literal.rstrip("/")
    if not literal:
        return p, ""
    rest = p[len(literal):]
    if literal in repo_files or any(f.startswith(literal + "/") for f in repo_files):
        return p, ""
    cands: set[str] = set()
    for f in repo_files:
        if f.endswith("/" + literal):
            cands.add(f)
        i = f.find("/" + literal + "/")
        while i != -1:
            cands.add(f[:i + 1 + len(literal)])
            i = f.find("/" + literal + "/", i + 1)
    if len(cands) == 1:
        return cands.pop() + rest, ""
    why = "no repo file ends with it" if not cands else f"{len(cands)} repo paths end with it"
    return p, f"{p}: {why}; kept as written"


def section_files(body: str, step_bodies: list[str]) -> list[str]:
    """A section's Files line plus every path on its steps' Files lines, in order, once each."""
    out: list[str] = []
    for text in [body, *step_bodies]:
        for raw in files_in(text):
            p = _norm(raw)
            if p and _PATH_LIKE.search(p) and p not in out:
                out.append(p)
    return out


def seat_lanes(plan: dict[str, Any] | None, repo_files: list[str] | None = None) -> list[dict[str, Any]]:
    """[{name, id, lane, warnings}] per seat of *plan*: ``section_files`` resolved against
    *repo_files* (the plan repo's ``git ls-files``)."""
    seats, _ = plan_seats(plan)
    out = []
    for s in seats:
        lane: list[str] = []
        warnings: list[str] = []
        for p in section_files(s["body"], [st["body"] for st in s["steps"]]):
            q, w = resolve_path(p, repo_files)
            if q not in lane:
                lane.append(q)
            if w:
                warnings.append(w)
        out.append({"name": s["name"], "id": s["id"], "lane": lane, "warnings": warnings})
    return out


def plan_overlaps(plan: dict[str, Any] | None, repo_files: list[str] | None = None) -> list[dict[str, str]]:
    """[{a, b, path}] for every path two sections' lanes (``seat_lanes``) both hold, unless it is
    a declared hand-over. ``path`` is the narrower of the two entries. Nov 5 2026: the I/I2
    overlap on ``agent_host/**`` was only found when seating was refused."""
    seats, expand = plan_seats(plan)
    lanes = {x["id"]: x["lane"] for x in seat_lanes(plan, repo_files)}
    for s in seats:
        s["lane"] = lanes.get(s["id"], s["lane"])
    deps = step_deps(seats, expand)
    return [{"a": c["seats"][0], "b": c["seats"][1], "path": c["file"]} for c in owner_conflicts(seats, deps)]
