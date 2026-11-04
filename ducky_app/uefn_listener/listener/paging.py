"""Page a list command inside UEFN: build only the rows a reply returns.

A census used to serialize every actor (12,040 rows, 9+ MB) and let the 2 MB send cap
(:mod:`listener.limits`) throw most of it away, paying the memory and the time inside the
editor first. :func:`page` walks the items once: a cheap ``keep`` test counts ``total``,
and the costly ``build`` runs only for the ``limit`` rows from ``offset``. The reply keeps
the ``total`` / ``returned`` / ``truncated`` keys callers and hint text already read, plus
``next_offset`` when there is more.

Pure Python, no ``unreal`` import.
"""

from __future__ import annotations

from typing import Any, Callable, Iterable, Optional

# 500 actor rows with the default fields stay far under the 2 MB reply cap.
DEFAULT_LIMIT = 500
MAX_LIMIT = 5000
PAGE_HINT = "page with offset/limit (next_offset), or filter by class/folder/label"


def window(offset: Any = 0, limit: Any = None, *, default: int = DEFAULT_LIMIT) -> tuple[int, int]:
    """``(offset, limit)`` made safe: no limit (or a negative one) is ``default``, and no
    page is bigger than :data:`MAX_LIMIT`."""
    try:
        off = max(0, int(offset or 0))
    except (TypeError, ValueError):
        off = 0
    try:
        lim = int(limit) if limit is not None else default
    except (TypeError, ValueError):
        lim = default
    if lim < 0:
        lim = default
    return off, min(lim, MAX_LIMIT)


def page(
    items: Iterable[Any],
    build: Callable[[Any], Any],
    *,
    offset: Any = 0,
    limit: Any = None,
    keep: Optional[Callable[[Any], bool]] = None,
    default: int = DEFAULT_LIMIT,
    count_all: bool = True,
) -> dict[str, Any]:
    """One pass over ``items``: ``rows`` built for the window only, and the page facts.

    ``keep(item)`` (cheap: a class name, a label) decides what counts. ``build(item)``
    makes a row; a row it returns as None is dropped (an actor gone mid-walk). With
    ``count_all=False`` the walk stops after the page and ``total`` is a lower bound
    (``total_exact`` False), for scans whose ``keep`` itself is costly.
    """
    off, lim = window(offset, limit, default=default)
    rows: list[Any] = []
    total = 0
    exact = True
    for item in items:
        if keep is not None and not keep(item):
            continue
        index = total
        total += 1
        if index < off:
            continue
        if len(rows) < lim:
            row = build(item)
            if row is not None:
                rows.append(row)
            continue
        if not count_all:
            exact = False
            break
    more = off + len(rows) < total or not exact
    out: dict[str, Any] = {
        "rows": rows,
        "total": total,
        "total_exact": exact,
        "returned": len(rows),
        "count": len(rows),
        "offset": off,
        "limit": lim,
        "truncated": more,
    }
    if more:
        out["next_offset"] = off + len(rows)
        out["hint"] = PAGE_HINT
    return out


def reply(key: str, paged: dict[str, Any], **extra: Any) -> dict[str, Any]:
    """The command's reply: ``rows`` under ``key`` plus the page facts and ``extra``."""
    out = {key: paged["rows"], **{k: v for k, v in paged.items() if k != "rows"}, **extra}
    if out.get("total_exact", True):
        out.pop("total_exact", None)
    return out
