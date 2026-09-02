"""Map UEFN Verse compiler error codes to one-line fix hints for the agent."""

from __future__ import annotations

import re
from collections import Counter

COMPILE_HINTS: dict[str, str] = {
    "3512": (
        "A helper with no effect specifier is no_rollback and cannot run inside if/[]/<decides>; "
        "add <transacts> (or <computes>) to the helper, or bind its result to a local before the if. "
        "Also raised for int `/` outside a failure context (use Floor[]) and for constructing a "
        "<transacts> class at module scope."
    ),
    "3582": (
        "Field initialisers must be literals/archetypes; a function call there is divergent. "
        "Set the value in OnBegin."
    ),
    "3593": (
        "Only top-level Assets-digest modules are importable and scoped classes cannot be "
        "constructed elsewhere; move the asset up a folder or expose a <public> module."
    ),
    "3588": (
        "Ambiguous identifier: a local/field shadows a module function (commonly Distance with "
        "SpatialMath imported). Rename it."
    ),
    "3532": (
        "Ambiguous identifier: a local/field shadows a module function (commonly Distance with "
        "SpatialMath imported) or a Content/Verse folder module. Rename it."
    ),
    "3104": "Dangling `=`: keep the function signature and `=` on one line; check for an empty body.",
    "3506": (
        "Unknown identifier/member: search_verse_digest before using it; check the using path "
        "(e.g. /Verse.org/SceneGraph for collision_point/FindSweepHits)."
    ),
    "3509": (
        "Type mismatch: int `/` yields rational (use Floor[x / 60.0] on a float); ToString has no "
        "logic overload; array literals are array{} not array:."
    ),
    "3524": "`for` needs an array, map or generator after ':'; check the import for the generator's type.",
    "3511": "<decides> functions are called with [] not ().",
    "3514": "`_` is reserved; name the binding.",
    "3100": (
        "Parse error: check 4-space indentation, `#` comments, and unbalanced braces. A lone `{}` on "
        "its own line under `if (...):`/`else:` is this error — put it on the head line: "
        "`if (set M[K] = V) {}`."
    ),
    "9002": (
        "Asset module not found in the Assets digest; the asset must exist in the project Content "
        "folder and be built once."
    ),
}

_CODE_RE = re.compile(r"Script error (\d+)")
_UNKNOWN_HINT = (
    "No canned hint for this code; read the message, then skill_read_subskill('verse','compile_errors') "
    "and search_verse_digest for any named identifier."
)


def extract_codes(message: str) -> Counter:
    """Count ``Script error NNNN`` codes in a compiler message / serialized result."""
    return Counter(_CODE_RE.findall(message or ""))


def hints_for(message: str) -> list[dict]:
    """``[{"code", "count", "hint", "subskill": "compile_errors"}]`` sorted by count desc."""
    counts = extract_codes(message)
    out: list[dict] = []
    for code, count in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])):
        out.append(
            {
                "code": code,
                "count": count,
                "hint": COMPILE_HINTS.get(code, _UNKNOWN_HINT),
                "subskill": "compile_errors",
            }
        )
    return out
