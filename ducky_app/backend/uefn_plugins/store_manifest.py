"""plugin.json checks: data declarations and reading the manifest out of a plugin zip.

Store plugins import these names from :mod:`backend.uefn_plugins.store`, which re-exports them.
"""

from __future__ import annotations

import io
import json
import re
import zipfile
from pathlib import Path
from typing import Any

# Manifest ``"data_locks": {"<key prefix>": "auto" | "required" | "none"}``: how a plugin's
# shared docs and files lock while one person edits them. Without an entry every item is
# ``auto``: the first change takes the lock (the write goes ahead either way) and teammates
# can't change the item until it's released. ``required`` waits for the lock and refuses the
# write without it; ``none`` never locks. A prefix is the start of a doc key or file path
# ('' for all).
DATA_LOCK_POLICIES = ("auto", "required", "none")
_DATA_LOCK_PREFIX_RE = re.compile(r"^[a-z0-9._/-]{0,256}$")
# Manifest ``"data_local": ["<key prefix>", ...]``: docs and files that belong to this PC
# (diagnostics, per-device state). They stay in the plugin's Local copy whatever copy it
# shows, never sync, and never count as team activity. A prefix is never '' (that would be
# all of the plugin's data: use ``"data_sharing": false`` for that).
_DATA_LOCAL_PREFIX_RE = re.compile(r"^[a-z0-9._/-]{1,256}$")


def _data_local_errors(manifest: dict[str, Any]) -> list[str]:
    if "data_local" not in manifest:
        return []
    local = manifest.get("data_local")
    if not isinstance(local, list):
        return ['plugin.json "data_local" must be a list of doc key or file path prefixes: ["qa.", "device/"].']
    errors = [
        f'plugin.json "data_local" entry {prefix!r} isn\'t the start of a doc key or file path: use a-z, 0-9, '
        "and . _ / - (1 to 256)."
        for prefix in local if not isinstance(prefix, str) or not _DATA_LOCAL_PREFIX_RE.match(prefix)
    ]
    if local and manifest.get("data_sharing") is False:
        errors.append('plugin.json has "data_local" but "data_sharing": false: all of it is Local already, so '
                      "remove one.")
    return errors


def manifest_data_errors(manifest: dict[str, Any]) -> list[str]:
    """What is wrong with a manifest's ``data_sharing``, ``data_locks``, ``data_local``,
    ``data_sync`` and ``team_folders`` (:mod:`backend.uefn_plugins.team_folders`), each with the fix."""
    from backend.uefn_plugins.team_folders import declaration_errors

    errors: list[str] = []
    sharing = manifest.get("data_sharing")
    if "data_sharing" in manifest and not isinstance(sharing, bool):
        errors.append(
            f'plugin.json "data_sharing" is {sharing!r}: use true (offer a shared space even before the plugin '
            "stores data) or false (always Local), or leave it out."
        )
    errors += declaration_errors(manifest)
    errors += _data_local_errors(manifest)
    from backend.uefn_plugins.data_shape import data_sync_errors

    errors += data_sync_errors(manifest)
    if "data_live" in manifest and not isinstance(manifest.get("data_live"), bool):
        errors.append(f'plugin.json "data_live" is {manifest.get("data_live")!r}: leave it out (teammates live in '
                      "one group get each change at once, over the group's link) or use false (changes go with sync "
                      "only).")
    elif manifest.get("data_live") is True and sharing is False:
        errors.append('plugin.json has "data_live" but "data_sharing": false: nothing is shared, so remove one.')
    if "data_locks" not in manifest:
        return errors
    locks = manifest.get("data_locks")
    if not isinstance(locks, dict):
        return [*errors, 'plugin.json "data_locks" must be an object: {"<doc key or file path prefix>": "auto", '
                         '"required" or "none"}.']
    for prefix, policy in locks.items():
        if not _DATA_LOCK_PREFIX_RE.match(str(prefix)):
            errors.append(
                f'plugin.json "data_locks" key {prefix!r} isn\'t the start of a doc key or file path: use a-z, 0-9, '
                "and . _ / - (256 at most)."
            )
        elif policy not in DATA_LOCK_POLICIES:
            errors.append(f'plugin.json "data_locks" {prefix!r} is {policy!r}: use "auto", "required" or "none".')
    if locks and sharing is False:
        errors.append('plugin.json has "data_locks" but "data_sharing": false: nothing is shared, so remove one.')
    return errors


def _zip_integrity_error(zf: zipfile.ZipFile) -> str | None:
    """Return a human error if the zip fails CRC / structural checks. Never extracts."""
    try:
        bad = zf.testzip()
    except zipfile.BadZipFile as exc:
        return f"Invalid zip: {exc}"
    except Exception as exc:  # noqa: BLE001 — treat any test failure as refuse-install
        return f"Corrupt zip: {exc}"
    if bad:
        return f"Corrupt zip entry {bad}: Bad CRC-32 (refusing install)"
    return None


def parse_plugin_manifest_from_zip(data: bytes) -> dict[str, Any]:
    # Read through the store module so its size cap stays patchable in one place.
    from backend.uefn_plugins import store

    if len(data) > store.MAX_PLUGIN_ZIP_BYTES:
        raise ValueError(f"zip exceeds max size ({store.MAX_PLUGIN_ZIP_BYTES} bytes)")
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        integrity = _zip_integrity_error(zf)
        if integrity:
            raise ValueError(integrity)
        names = zf.namelist()
        manifest_name = None
        for name in names:
            # Allow plugin.json at root or one folder deep (discord/plugin.json).
            parts = Path(name).parts
            if parts and parts[-1] == store.PLUGIN_MANIFEST and len(parts) <= 2 and ".." not in parts:
                manifest_name = name
                break
        if not manifest_name:
            raise ValueError("zip missing plugin.json")
        raw = zf.read(manifest_name).decode("utf-8")
        manifest = json.loads(raw)
        if not isinstance(manifest, dict):
            raise ValueError("plugin.json must be an object")
        store.normalize_plugin_id(str(manifest.get("id") or ""))
        return manifest
