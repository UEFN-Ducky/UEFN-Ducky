"""Write lanes: which project paths a group member may write.

A lane is a list of gitignore-style globs (``write_allowed``). Semantics are a
public contract (ADR 0001, ``schemas/lane_set.schema.json``):

- ``**`` spans directories; ``*`` and ``?`` never cross ``/``
- a bare directory (``Content/Verse/Shop``) means ``Content/Verse/Shop/**``
- matching is case-insensitive (UEFN projects live on Windows)
- ``None`` = unrestricted, ``[]`` = read-only

Storage of lanes is the group roster (frontend); this module only knows how to
match, compare and enforce them. A ``LaneProvider`` supplies the lane for a
conversation; the roster provider is registered at startup and a plugin may
register another (an external orchestrator's manifest, say).

Enforcement runs as a ``WritePolicy`` inside the pipeline (authoritative) and
as an early pre-dispatch check in the agent loop (fast, event-emitting).
``write_lanes_mode`` selects ``off`` / ``shadow`` (flag only) / ``enforce``.
"""

from __future__ import annotations

import re
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable, Iterable, Mapping, Protocol, Sequence, runtime_checkable

from backend.workspace.events import GUARD_LANE_DENIED
from backend.workspace.paths import normalize_rel
from backend.workspace.policy import ALLOW, Decision, WriteRequest

MODE_OFF = "off"
MODE_SHADOW = "shadow"
MODE_ENFORCE = "enforce"
MODES = (MODE_OFF, MODE_SHADOW, MODE_ENFORCE)

POLICY_NAME = "lane"

def _default_mode() -> str:
    return MODE_SHADOW


_mode_source: Callable[[], str] = _default_mode


def set_mode_source(getter: Callable[[], str]) -> None:
    """Install the reader for write_lanes_mode (the composition root passes the settings reader)."""
    global _mode_source
    _mode_source = getter


def current_mode() -> str:
    try:
        mode = str(_mode_source() or "").strip().lower()
    except Exception:  # noqa: BLE001 - never let a settings read block a write
        return MODE_SHADOW
    return mode if mode in MODES else MODE_SHADOW

_WILDCARDS = set("*?[")


# ------------------------------------------------------------------- globs


class LaneGlobError(ValueError):
    """A lane pattern that can never be safe (escapes, absolute, empty)."""


def normalize_glob(pattern: str) -> str:
    """Canonical form of one lane pattern; raises LaneGlobError when unsafe."""
    p = (pattern or "").strip().replace("\\", "/")
    while p.startswith("./"):
        p = p[2:]
    if not p:
        raise LaneGlobError("empty lane pattern")
    if p.startswith("/") or (len(p) > 1 and p[1] == ":"):
        raise LaneGlobError(f"lane pattern must be project-relative: {pattern!r}")
    if any(seg == ".." for seg in p.split("/")):
        raise LaneGlobError(f"lane pattern must not contain '..': {pattern!r}")
    p = p.rstrip("/")
    while "//" in p:
        p = p.replace("//", "/")
    last = p.rsplit("/", 1)[-1]
    looks_like_dir = not any(ch in _WILDCARDS for ch in p) and "." not in last
    if looks_like_dir:
        p = f"{p}/**"
    return p


def normalize_lane(patterns: Iterable[str] | str | None) -> list[str] | None:
    """None stays None (unrestricted); strings split on newlines/commas; dedupe, sort."""
    if patterns is None:
        return None
    if isinstance(patterns, str):
        raw = [s for s in re.split(r"[\n,]", patterns)]
    else:
        raw = [str(s) for s in patterns]
    out: list[str] = []
    for item in raw:
        if not item.strip():
            continue
        norm = normalize_glob(item)
        if norm not in out:
            out.append(norm)
    return sorted(out)


def _translate(pattern: str) -> str:
    parts: list[str] = []
    segments = pattern.split("/")
    for i, seg in enumerate(segments):
        last = i == len(segments) - 1
        if seg == "**":
            parts.append("(?:[^/]+/)*" if not last else ".*")
            continue
        buf = []
        for ch in seg:
            if ch == "*":
                buf.append("[^/]*")
            elif ch == "?":
                buf.append("[^/]")
            else:
                buf.append(re.escape(ch))
        parts.append("".join(buf) + ("" if last else "/"))
    return "".join(parts)


_compiled: dict[str, re.Pattern[str]] = {}


def compile_glob(pattern: str) -> re.Pattern[str]:
    norm = normalize_glob(pattern)
    cached = _compiled.get(norm)
    if cached is None:
        body = _translate(norm)
        # A directory glob also matches the directory itself (rename/delete of the folder).
        if norm.endswith("/**"):
            head = _translate(norm[: -len("/**")])
            body = f"(?:{body})|(?:{head})"
        cached = re.compile(f"^(?:{body})$", re.IGNORECASE)
        _compiled[norm] = cached
    return cached


def match(pattern: str, rel_path: str) -> bool:
    return compile_glob(pattern).match(normalize_rel(rel_path)) is not None


def any_match(patterns: Iterable[str], rel_path: str) -> bool:
    return any(match(p, rel_path) for p in patterns)


def literal_prefix(pattern: str) -> str:
    """Path segments before the first wildcard segment, as ``a/b/c`` (may be empty)."""
    norm = normalize_glob(pattern)
    kept: list[str] = []
    for seg in norm.split("/"):
        if any(ch in _WILDCARDS for ch in seg):
            break
        kept.append(seg)
    return "/".join(kept)


def is_dir_glob(pattern: str) -> bool:
    return normalize_glob(pattern).endswith("/**")


def is_exact_file(pattern: str) -> bool:
    norm = normalize_glob(pattern)
    return not any(ch in _WILDCARDS for ch in norm)


def _has_mid_wildcard(pattern: str) -> bool:
    norm = normalize_glob(pattern)
    segments = norm.split("/")
    return any(any(ch in _WILDCARDS for ch in seg) for seg in segments[:-1] if seg != "**") or (
        "**" in segments[:-1]
    )


def _is_prefix(a: str, b: str) -> bool:
    a, b = a.lower(), b.lower()
    return a == b or (a and b.startswith(a + "/")) or a == ""


def overlap(a: str, b: str) -> str | None:
    """How two lane patterns collide: identical | contains | matches | maybe | None.

    identical/contains/matches are errors (a member could write another's file);
    maybe (mid-path wildcards with related literal prefixes) is a warning that
    needs ``force``. Glob-vs-glob intersection is undecidable in general; this
    is the practical rule set the ADR commits to.
    """
    na, nb = normalize_glob(a), normalize_glob(b)
    if na.lower() == nb.lower():
        return "identical"
    pa, pb = literal_prefix(na), literal_prefix(nb)
    related = _is_prefix(pa, pb) or _is_prefix(pb, pa)
    # "Plain" directory lanes (only a trailing /**) have decidable containment.
    plain_a = is_dir_glob(na) and not _has_mid_wildcard(na)
    plain_b = is_dir_glob(nb) and not _has_mid_wildcard(nb)
    if plain_a and plain_b and related:
        return "contains"
    if plain_a and is_exact_file(nb) and _is_prefix(pa, pb):
        return "contains"
    if plain_b and is_exact_file(na) and _is_prefix(pb, pa):
        return "contains"
    if is_exact_file(na) and match(nb, na):
        return "matches"
    if is_exact_file(nb) and match(na, nb):
        return "matches"
    if (_has_mid_wildcard(na) or _has_mid_wildcard(nb)) and related:
        return "maybe"
    return None


def check_lane_set(lanes: Mapping[str, Sequence[str] | None]) -> dict[str, list[str]]:
    """Pairwise overlap across members of one group → {"errors": [...], "warnings": [...]}."""
    errors: list[str] = []
    warnings: list[str] = []
    items = [(member, list(lane)) for member, lane in lanes.items() if lane]
    for i, (ma, la) in enumerate(items):
        for mb, lb in items[i + 1 :]:
            for ga in la:
                for gb in lb:
                    kind = overlap(ga, gb)
                    if kind is None:
                        continue
                    text = f"{ma}: {ga} overlaps {mb}: {gb} ({kind})"
                    (warnings if kind == "maybe" else errors).append(text)
    return {"errors": errors, "warnings": warnings}


# ---------------------------------------------------------------- lane sets


def lane_set_view(group_id: str, members: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """Schema ``lane_set/1`` for a roster (normalized member dicts)."""
    lanes: dict[str, Any] = {}
    for m in members:
        conv_id = str(m.get("member_conv_id") or "").strip()
        if not conv_id:
            continue
        row: dict[str, Any] = {"write_allowed": m.get("write_allowed")}
        for key in ("name", "ducky_name", "lane_set_by"):
            value = m.get(key)
            if isinstance(value, str) and value:
                row["set_by" if key == "lane_set_by" else key] = value
        set_at = m.get("lane_set_at")
        if isinstance(set_at, (int, float)) and set_at:
            row["set_at"] = float(set_at)
        lanes[conv_id] = row
    return {"schema_version": 1, "group_id": group_id, "lanes": lanes}


def set_member_lane(
    members: Sequence[Mapping[str, Any]],
    member_conv_id: str,
    write_allowed: Iterable[str] | str | None,
    *,
    set_by: str,
    now: float,
    force: bool = False,
) -> tuple[list[dict[str, Any]], list[str]]:
    """New roster rows with the lane applied, plus warnings. Raises ValueError on overlap."""
    lane = normalize_lane(write_allowed)
    mid = (member_conv_id or "").strip()
    if not any(str(m.get("member_conv_id") or "") == mid for m in members):
        raise ValueError("Member not in this group")
    proposed = {str(m.get("member_conv_id") or ""): m.get("write_allowed") for m in members}
    proposed[mid] = lane
    verdict = check_lane_set(proposed)
    if verdict["errors"]:
        raise ValueError("Lane overlaps another member's lane: " + "; ".join(verdict["errors"]))
    if verdict["warnings"] and not force:
        raise ValueError(
            "Lane may overlap another member's lane (pass force=true to accept): "
            + "; ".join(verdict["warnings"])
        )
    rows: list[dict[str, Any]] = []
    for m in members:
        row = dict(m)
        if str(row.get("member_conv_id") or "") == mid:
            row["write_allowed"] = lane
            row["lane_set_by"] = set_by
            row["lane_set_at"] = float(now)
        rows.append(row)
    return rows, verdict["warnings"]


# ------------------------------------------------------------- resolution


@runtime_checkable
class LaneProvider(Protocol):
    def lane_for(self, conv_id: str) -> tuple[str, ...] | None:
        """The member's lane, or None when this provider does not govern the chat."""
        ...


_providers: list[LaneProvider] = []
_providers_lock = threading.Lock()
_cache: dict[str, tuple[float, tuple[str, ...] | None]] = {}
CACHE_TTL_S = 2.0


def register_lane_provider(provider: LaneProvider) -> None:
    with _providers_lock:
        if provider not in _providers:
            _providers.append(provider)


def unregister_lane_provider(provider: LaneProvider) -> None:
    with _providers_lock:
        if provider in _providers:
            _providers.remove(provider)


def invalidate_lane_cache(conv_id: str = "") -> None:
    with _providers_lock:
        if conv_id:
            _cache.pop(conv_id, None)
        else:
            _cache.clear()


def resolve_lane(conv_id: str, *, now: float | None = None) -> tuple[str, ...] | None:
    """First provider that governs the chat wins; cached briefly so a leader's change
    takes effect on the member's next tool call without a restart."""
    if not conv_id:
        return None
    t = time.time() if now is None else now
    with _providers_lock:
        hit = _cache.get(conv_id)
        providers = list(_providers)
    if hit is not None and t - hit[0] < CACHE_TTL_S:
        return hit[1]
    lane: tuple[str, ...] | None = None
    for provider in providers:
        try:
            found = provider.lane_for(conv_id)
        except Exception:  # noqa: BLE001 - a broken provider must not block writes
            continue
        if found is not None:
            lane = tuple(found)
            break
    with _providers_lock:
        _cache[conv_id] = (t, lane)
    return lane


def reset_for_tests() -> None:
    global _mode_source
    with _providers_lock:
        _providers.clear()
        _cache.clear()
    _mode_source = _default_mode


# ----------------------------------------------------------------- policy


@dataclass
class LanePolicy:
    """Deny (enforce) or flag (shadow) writes outside the caller's lane."""

    mode: Callable[[], str]
    name: str = POLICY_NAME

    def check(self, request: WriteRequest) -> Decision:
        ctx = request.ctx
        if ctx is None:
            return ALLOW
        mode = (self.mode() or MODE_SHADOW).lower()
        if mode == MODE_OFF:
            return ALLOW
        lane = ctx.lane if ctx.lane is not None else resolve_lane(ctx.conv_id)
        if lane is None:
            return ALLOW
        outside = [p for p in request.paths if not any_match(lane, p)]
        if not outside:
            return Decision(policy=self.name)
        reason = denial_text(outside[0], lane, ctx.leader_conv_id)
        details = {
            "kind": GUARD_LANE_DENIED,
            "lane": list(lane),
            "paths": outside,
            "leader_conv_id": ctx.leader_conv_id,
            "mode": mode,
        }
        if mode == MODE_ENFORCE:
            return Decision(
                allow=False,
                policy=self.name,
                reason=reason,
                hint="Do not retry this path. Stay inside your lane or ask the group leader to widen it.",
                details=details,
            )
        return Decision(policy=self.name, shadow_violation=True, reason=reason, details=details)


def denial_text(path: str, lane: Sequence[str], leader_conv_id: str = "") -> str:
    shown = ", ".join(lane) if lane else "(read-only)"
    leader = f" (leader chat {leader_conv_id})" if leader_conv_id else ""
    return (
        f'Out of lane: "{path}" is not in your write lane [{shown}]. Only the group '
        f"leader{leader} or the user can widen it — ask in the group chat or continue "
        "inside your lane. Do not retry this path."
    )


# ------------------------------------------------- pre-dispatch path extraction


def _join(parent: str, name: str) -> str:
    parent = normalize_rel(str(parent or "")).strip("/")
    name = str(name or "").strip().replace("\\", "/").strip("/")
    return f"{parent}/{name}" if parent else name


def _create_verse(args: Mapping[str, Any]) -> list[str]:
    name = str(args.get("name") or "")
    if name and not name.lower().endswith(".verse"):
        name = f"{name}.verse"
    return [_join(str(args.get("parent_relative") or "Content"), name)]


def _create_file(args: Mapping[str, Any]) -> list[str]:
    name = str(args.get("name") or "")
    if name and "." not in name.rsplit("/", 1)[-1]:
        name = f"{name}.txt"
    return [_join(str(args.get("parent_relative") or "Content"), name)]


def _rename(args: Mapping[str, Any]) -> list[str]:
    src = normalize_rel(str(args.get("source_relative") or ""))
    parent = src.rsplit("/", 1)[0] if "/" in src else ""
    return [src, _join(parent, str(args.get("new_name") or ""))]


def _move(args: Mapping[str, Any]) -> list[str]:
    src = normalize_rel(str(args.get("source_relative") or ""))
    base = src.rsplit("/", 1)[-1]
    return [src, _join(str(args.get("dest_parent_relative") or "Content"), base)]


PATH_ARGS: dict[str, Callable[[Mapping[str, Any]], list[str]]] = {
    "workspace_write_file": lambda a: [normalize_rel(str(a.get("relative_path") or a.get("path") or ""))],
    "create_project_verse_file": _create_verse,
    "create_project_file": _create_file,
    "rename_project_entry": _rename,
    "move_project_entry": _move,
    "delete_project_entry": lambda a: [normalize_rel(str(a.get("relative_path") or ""))],
    "verse_test_scaffold": lambda a: ["Verse/DuckyTests/ducky_test_device.verse"],
}


def extract_paths(tool_name: str, args: Mapping[str, Any] | None) -> tuple[str, list[str]]:
    """(effective tool name, project paths it would write). Unwraps ducky_call_tool."""
    name = str(tool_name or "")
    payload: Mapping[str, Any] = args or {}
    if name == "ducky_call_tool" and isinstance(payload.get("arguments"), Mapping):
        name = str(payload.get("name") or "")
        payload = payload.get("arguments") or {}
    if "__" in name:
        name = name.rsplit("__", 1)[-1]
    extractor = PATH_ARGS.get(name)
    if extractor is None:
        return name, []
    try:
        return name, [p for p in extractor(payload) if p]
    except Exception:  # noqa: BLE001 - malformed args are the tool's problem, not ours
        return name, []


def lane_block_reason(
    tool_name: str,
    args: Mapping[str, Any] | None,
    *,
    mode: Callable[[], str],
) -> Decision | None:
    """Early denial for the agent loop; None when the call may proceed."""
    from backend.workspace import identity

    ctx = identity.current()
    if ctx is None:
        return None
    name, paths = extract_paths(tool_name, args)
    if not paths:
        return None
    decision = LanePolicy(mode=mode).check(WriteRequest(op="write", paths=tuple(paths), tool=name, ctx=ctx))
    return None if decision.allow else decision
