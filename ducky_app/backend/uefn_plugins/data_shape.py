"""How a plugin keeps its records, measured on this PC.

A team gets one change per item (a doc or a file): each is listed in the Team menu, kept in
history and fetched by every teammate. A plugin that keeps one small doc per event (a stroke,
a log line, an undo step) fills all three with thousands of them. ``ducky_plugin_validate``
names such a key prefix and the fix (:func:`warnings`).
"""

from __future__ import annotations

import re
from typing import Any, Iterable

MANY = 300  # docs under one key prefix, in one copy, before it is worth a word
SMALL_BYTES = 2048  # ... when half of them are no bigger than this


# Manifest ``"data_sync": {"<doc key prefix>": "batched"}``: log-style docs (an activity feed,
# records nobody watches being typed). A team gets one change per item, so the plugin that
# shares the space sends these together every few minutes, as log rows and not as changes of
# their own. ``"log"`` means the same.
DATA_SYNC_MODES = ("batched", "log")
_DATA_SYNC_PREFIX_RE = re.compile(r"^[a-z0-9._-]{1,128}$")


def data_sync_errors(manifest: dict[str, Any]) -> list[str]:
    if "data_sync" not in manifest:
        return []
    sync = manifest.get("data_sync")
    if not isinstance(sync, dict):
        return ['plugin.json "data_sync" must be an object: {"<doc key prefix>": "batched"}.']
    errors = []
    for prefix, mode in sync.items():
        if not _DATA_SYNC_PREFIX_RE.match(str(prefix)):
            errors.append(f'plugin.json "data_sync" key {prefix!r} isn\'t the start of a doc key: use a-z, 0-9, '
                          "and . _ - (1 to 128).")
        elif mode not in DATA_SYNC_MODES:
            errors.append(f'plugin.json "data_sync" {prefix!r} is {mode!r}: use "batched".')
    if sync and manifest.get("data_sharing") is False:
        errors.append('plugin.json has "data_sync" but "data_sharing": false: nothing is shared, so remove one.')
    return errors


def data_sync(manifest: dict[str, Any]) -> tuple[str, ...]:
    """The doc key prefixes a manifest's ``data_sync`` marks as a log (sent together, as log rows)."""
    sync = manifest.get("data_sync")
    if not isinstance(sync, dict) or manifest.get("data_sharing") is False:
        return ()
    return tuple(sorted(p for p, mode in sync.items()
                        if isinstance(p, str) and _DATA_SYNC_PREFIX_RE.match(p) and mode in DATA_SYNC_MODES))


def key_group(key: str) -> str:
    """The key's first part with its dot: ``op.d1.00000007`` → ``op.`` (a key with no dot: itself)."""
    head, dot, _ = key.partition(".")
    return head + dot


def small_item_groups(sizes: Iterable[tuple[str, int]], skip: Iterable[str] = ()) -> list[tuple[str, int, int]]:
    """``(prefix, docs, median bytes)`` of the key groups that hold :data:`MANY` or more docs,
    half of them :data:`SMALL_BYTES` or smaller, biggest first. Keys under a ``skip`` prefix
    (this PC's own, or a declared log) are left out."""
    skip = tuple(p for p in skip if p)
    groups: dict[str, list[int]] = {}
    for key, size in sizes:
        if not key.startswith(skip):
            groups.setdefault(key_group(key), []).append(int(size or 0))
    out = []
    for group, found in groups.items():
        found.sort()
        median = found[len(found) // 2]
        if len(found) >= MANY and median <= SMALL_BYTES:
            out.append((group, len(found), median))
    return sorted(out, key=lambda g: (-g[1], g[0]))


def message(group: str, docs: int, median: int) -> str:
    return (
        f'{docs:,} small items under "{group}" (about {median:,} bytes each): a team gets one change per item, '
        "so these fill its change list and history. Keep one item per thing people edit and gather the "
        "events into it (a page of about 50, or one list with a cap). Or, when they belong to this PC (undo "
        f'steps, job progress, diagnostics), list "{group}" in "data_local"; when they are a log teammates read, '
        f'add "data_sync": {{"{group}": "batched"}} and keep only the newest.'
    )


def warnings(plugin_id: str, manifest: dict[str, Any]) -> list[str]:
    """One line per key prefix of a shared plugin that holds many small docs on this PC (the
    copy with the most of them counts: Local and each team copy hold the same records)."""
    if manifest.get("data_sharing") is not True:
        return []
    from backend.store.repos import plugin_data as repo
    from backend.uefn_plugins import scopes
    from backend.uefn_plugins.store import data_local

    skip = (*data_local(manifest), *data_sync(manifest))
    account = scopes.account_id()
    worst: dict[str, tuple[int, int]] = {}
    for scope in repo.scope_ids(account):
        rows = repo.rows(account, scope, plugin_id, "doc", values=False)
        for group, docs, median in small_item_groups(((str(r["key"]), int(r["size"] or 0)) for r in rows), skip):
            if docs > worst.get(group, (0, 0))[0]:
                worst[group] = (docs, median)
    return [message(group, docs, median) for group, (docs, median) in sorted(worst.items(), key=lambda g: -g[1][0])]
