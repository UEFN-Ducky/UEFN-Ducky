"""Plugin version helpers — legacy ints (7) and semver strings (1.0.8).

Legacy bare ints sort as 0.0.N so 1.0.(N+1) is always newer after migration.
"""

from __future__ import annotations


def parse_plugin_version(raw: object) -> tuple[int, int, int]:
    if raw is None or isinstance(raw, bool):
        return (0, 0, 0)
    if isinstance(raw, int):
        return (0, 0, max(0, raw))
    if isinstance(raw, float):
        return (0, 0, max(0, int(raw)))
    s = str(raw).strip()
    if not s:
        return (0, 0, 0)
    if s.isdigit():
        return (0, 0, int(s))
    base = s.split("+", 1)[0].split("-", 1)[0]
    parts = base.split(".")
    nums: list[int] = []
    for part in parts[:3]:
        if not part.isdigit():
            return (0, 0, 0)
        nums.append(int(part))
    while len(nums) < 3:
        nums.append(0)
    return (nums[0], nums[1], nums[2])


def format_plugin_version(raw: object) -> str:
    """Display string: keep semver as-is; legacy int stays a bare number."""
    if isinstance(raw, str):
        s = raw.strip()
        if s and not s.isdigit() and parse_plugin_version(s) != (0, 0, 0):
            return s.split("+", 1)[0].split("-", 1)[0]
    a, b, c = parse_plugin_version(raw)
    if a == 0 and b == 0:
        return str(c)
    return f"{a}.{b}.{c}"


def plugin_version_rank(raw: object) -> int:
    """Comparable rank for update-available checks."""
    a, b, c = parse_plugin_version(raw)
    return a * 1_000_000 + b * 1_000 + c


def plugin_version_newer(remote: object, local: object) -> bool:
    return plugin_version_rank(remote) > plugin_version_rank(local)
