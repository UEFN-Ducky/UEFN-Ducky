"""Desktop plugin pack storage under AppData (mirrors skill / mcp_plugins layout)."""

from __future__ import annotations

import errno
import io
import json
import logging
import re
import shutil
import threading
import time
import zipfile
from pathlib import Path
from typing import Any

from backend.skills.store import appdata_dir
from backend.uefn_plugins.plugin_version import format_plugin_version

PLUGIN_MANIFEST = "plugin.json"
UEFN_PLUGINS_DIR = "uefn_plugins"
AI_PLUGINS_DIR = "ai_plugins"
PLUGIN_SKILLS_DIR = "skills"
PLUGIN_SKILL_FILE = "SKILL.md"
_PLUGIN_ID_RE = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
# Sources that need the one-time user trust prompt on first enable.
_TRUST_REQUIRED_SOURCES = frozenset({"local", "ai"})
# Art-heavy plugins can carry complete card catalogs. Keep a bounded archive
# limit while allowing their image bundles to install.
MAX_PLUGIN_ZIP_BYTES = 100 * 1024 * 1024
# Zip-bomb guard: extraction happens at install, before the local-trust prompt.
MAX_PLUGIN_UNCOMPRESSED_BYTES = 4 * MAX_PLUGIN_ZIP_BYTES

# fingerprint -> plugin-owned skill rows (see list_plugin_owned_skills).
_OWNED_SKILLS_CACHE: dict[tuple[tuple[str, int], ...], list[dict[str, str]]] = {}
_OWNED_SKILLS_LOCK = threading.Lock()
_FINGERPRINT: tuple[tuple[str, int], ...] | None = None
_FINGERPRINT_ROOT = ""
_FINGERPRINT_AT = 0.0
_FINGERPRINT_TTL = 1.0

# One plugin tree mutation at a time. Update All used to overlap the previous
# install's skill-sync (open modules.md) with the next plugin's rmtree → WinError 32.
_PLUGIN_TREE_LOCK = threading.RLock()
_SKILL_REFRESH_LOCK = threading.Lock()
_SKILL_REFRESH_WANTED = False
_SKILL_REFRESH_THREAD: threading.Thread | None = None
# Windows: 32 sharing violation, 33 lock violation, 5 access denied (Defender).
_WIN_LOCK_CODES = frozenset({5, 32, 33})


def appdata_uefn_plugins_dir() -> Path:
    return appdata_dir() / UEFN_PLUGINS_DIR


def app_too_old_for(manifest: dict[str, Any]) -> str:
    """Why this Ducky can't run the plugin (its min_app_version is newer), or ''."""
    from backend.uefn_plugins.plugin_version import parse_plugin_version

    need = str(manifest.get("min_app_version") or "").strip()
    if not need or parse_plugin_version(need) == (0, 0, 0):
        return ""
    try:
        from frontend import __version__ as have
    except Exception:
        return ""
    if parse_plugin_version(have) == (0, 0, 0) or parse_plugin_version(have) >= parse_plugin_version(need):
        return ""
    label = str(manifest.get("label") or manifest.get("id") or "This plugin")
    version = str(manifest.get("version") or "").strip()
    name = f"{label} {version}".strip()
    return f"{name} needs UEFN Ducky {need} or newer (you have {have}). Update UEFN Ducky, then install it."


def appdata_ai_plugins_dir() -> Path:
    """Shared per-install draft workspace for AI-authored plugins (not loaded directly)."""
    return appdata_dir() / AI_PLUGINS_DIR


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
    """What is wrong with a manifest's ``data_sharing``, ``data_locks``, ``data_local`` and
    ``team_folders`` (:mod:`backend.uefn_plugins.team_folders`), each with the fix."""
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


def data_locks(manifest: dict[str, Any]) -> dict[str, str]:
    """The valid entries of a manifest's ``data_locks`` (``{prefix: policy}``)."""
    locks = manifest.get("data_locks")
    if not isinstance(locks, dict) or manifest.get("data_sharing") is False:
        return {}
    return {str(prefix): str(policy) for prefix, policy in locks.items()
            if _DATA_LOCK_PREFIX_RE.match(str(prefix)) and policy in DATA_LOCK_POLICIES}


def data_live(manifest: dict[str, Any]) -> bool:
    """While teammates are live in one group, each change to a shared plugin's docs and files
    goes straight to the group over its peer-to-peer link and lands there at once (sync still
    records it); inside the group the newest change of an item wins and nobody locks anyone
    out. Every plugin that shares its data, unless its manifest says ``"data_live": false``."""
    return manifest.get("data_live") is not False and manifest.get("data_sharing") is not False


def data_local(manifest: dict[str, Any]) -> tuple[str, ...]:
    """The valid prefixes of a manifest's ``data_local``."""
    local = manifest.get("data_local")
    if not isinstance(local, list):
        return ()
    return tuple(sorted({p for p in local if isinstance(p, str) and _DATA_LOCAL_PREFIX_RE.match(p)}))


def normalize_plugin_id(plugin_id: str) -> str:
    pid = (plugin_id or "").strip().lower().replace(" ", "_")
    if not _PLUGIN_ID_RE.match(pid):
        raise ValueError(f"Invalid plugin id: {plugin_id!r}")
    return pid


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        if path.is_file():
            data = json.loads(path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else None
    except (OSError, json.JSONDecodeError):
        pass
    return None


def _write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def _is_win_lock_error(exc: BaseException) -> bool:
    if isinstance(exc, PermissionError):
        return True
    if not isinstance(exc, OSError):
        return False
    if getattr(exc, "winerror", None) in _WIN_LOCK_CODES:
        return True
    return getattr(exc, "errno", None) in (errno.EACCES, errno.EPERM, errno.EBUSY)


def _rmtree_retry(path: Path, *, attempts: int = 10, ignore_final: bool = False) -> None:
    """Delete a plugin folder; retry Windows sharing violations from skill-sync / AV."""
    last: OSError | None = None
    for i in range(max(1, attempts)):
        try:
            if not path.exists():
                return
            shutil.rmtree(path)
            return
        except OSError as exc:
            last = exc
            if not _is_win_lock_error(exc) or i == attempts - 1:
                break
            time.sleep(0.05 * (2 ** min(i, 4)))
    if ignore_final:
        shutil.rmtree(path, ignore_errors=True)
        return
    if last is not None:
        raise last


def _rename_retry(src: Path, dst: Path, *, attempts: int = 10) -> None:
    """Move a plugin folder; retry Windows sharing violations from skill-sync / AV."""
    for i in range(max(1, attempts)):
        try:
            src.replace(dst)
            return
        except OSError as exc:
            if not _is_win_lock_error(exc) or i == attempts - 1:
                raise
            time.sleep(0.05 * (2 ** min(i, 4)))


def _write_bytes_retry(path: Path, payload: bytes, *, attempts: int = 8) -> None:
    for i in range(max(1, attempts)):
        try:
            path.write_bytes(payload)
            return
        except OSError as exc:
            if not _is_win_lock_error(exc) or i == attempts - 1:
                raise
            time.sleep(0.05 * (2 ** min(i, 4)))


def load_plugin_manifest(plugin_id: str) -> dict[str, Any] | None:
    pid = normalize_plugin_id(plugin_id)
    return _read_json(appdata_uefn_plugins_dir() / pid / PLUGIN_MANIFEST)


def plugin_dir(plugin_id: str) -> Path:
    return appdata_uefn_plugins_dir() / normalize_plugin_id(plugin_id)


_PLUGIN_ICON_CACHE: dict[str, tuple[float, str]] = {}


def plugin_icon_data_url(plugin_id: str) -> str:
    """data: URL for assets/icon.* — empty if missing. Cached by mtime."""
    import base64

    try:
        pid = normalize_plugin_id(plugin_id)
    except ValueError:
        return ""
    root = plugin_dir(pid)
    candidates = (
        ("assets/icon.svg", "image/svg+xml"),
        ("assets/icon.png", "image/png"),
        ("assets/icon.jpg", "image/jpeg"),
        ("assets/icon.jpeg", "image/jpeg"),
        ("icon.svg", "image/svg+xml"),
        ("icon.png", "image/png"),
    )
    newest = 0.0
    hit: tuple[Path, str] | None = None
    for rel, mime in candidates:
        path = root / rel
        try:
            st = path.stat()
        except OSError:
            continue
        if st.st_mtime >= newest:
            newest = st.st_mtime
            hit = (path, mime)
            break
    if hit is None:
        _PLUGIN_ICON_CACHE.pop(pid, None)
        return ""
    cached = _PLUGIN_ICON_CACHE.get(pid)
    if cached and cached[0] == newest:
        return cached[1]
    path, mime = hit
    try:
        raw = path.read_bytes()
    except OSError:
        return ""
    if not raw or len(raw) > 64 * 1024:
        return ""
    url = f"data:{mime};base64,{base64.b64encode(raw).decode('ascii')}"
    _PLUGIN_ICON_CACHE[pid] = (newest, url)
    return url


def get_enabled_plugin_ids() -> list[str]:
    from frontend.settings import PanelSettings

    settings = PanelSettings.load()
    raw = getattr(settings, "enabled_uefn_plugins", None)
    if raw is None:
        # Migration: enable every installed plugin that declares default_enabled.
        out: list[str] = []
        root = appdata_uefn_plugins_dir()
        if root.is_dir():
            for child in sorted(root.iterdir()):
                if not child.is_dir():
                    continue
                m = _read_json(child / PLUGIN_MANIFEST)
                if m and bool(m.get("default_enabled", True)):
                    try:
                        out.append(normalize_plugin_id(str(m.get("id") or child.name)))
                    except ValueError:
                        continue
        return out
    enabled: list[str] = []
    for item in raw:
        try:
            enabled.append(normalize_plugin_id(str(item)))
        except ValueError:
            continue
    return enabled


def is_plugin_installed(plugin_id: str) -> bool:
    try:
        return load_plugin_manifest(plugin_id) is not None
    except ValueError:
        return False


def list_uefn_plugins() -> list[dict[str, Any]]:
    root = appdata_uefn_plugins_dir()
    if not root.is_dir():
        return []
    enabled = set(get_enabled_plugin_ids())
    trusted = set(_trusted_local_ids())
    out: list[dict[str, Any]] = []
    for child in sorted(root.iterdir()):
        if not child.is_dir():
            continue
        manifest = _read_json(child / PLUGIN_MANIFEST)
        if not manifest:
            continue
        try:
            pid = normalize_plugin_id(str(manifest.get("id") or child.name))
        except ValueError:
            continue
        source = str(manifest.get("source") or "local")
        out.append(
            {
                "id": pid,
                "label": str(manifest.get("label") or pid),
                "description": str(manifest.get("description") or ""),
                "version": format_plugin_version(manifest.get("version")),
                "kind": str(manifest.get("kind") or "plugin"),
                "source": source,
                "enabled": pid in enabled,
                "default_enabled": bool(manifest.get("default_enabled", True)),
                "local_trusted": pid in trusted,
                "contributes": manifest.get("contributes")
                if isinstance(manifest.get("contributes"), dict)
                else {},
                "secret_keys": list(manifest.get("secret_keys") or [])
                if isinstance(manifest.get("secret_keys"), list)
                else [],
                "path": str(child),
            }
        )
    return out


def _trusted_local_ids() -> list[str]:
    from frontend.settings import PanelSettings

    settings = PanelSettings.load()
    raw = getattr(settings, "trusted_local_uefn_plugins", None) or []
    out: list[str] = []
    for item in raw:
        try:
            out.append(normalize_plugin_id(str(item)))
        except ValueError:
            continue
    return out


def trust_local_plugin(plugin_id: str) -> None:
    from frontend.settings import PanelSettings, replace

    pid = normalize_plugin_id(plugin_id)
    settings = PanelSettings.load()
    current = list(getattr(settings, "trusted_local_uefn_plugins", None) or [])
    if pid not in current:
        current.append(pid)
        settings = replace(settings, trusted_local_uefn_plugins=current)
        settings.save()


# The person's "Trust AI plugin drafts this swarm builds, for testing" grant (the swarm's
# Build card): {"groups": [swarm group ids], "drafts": {plugin id: group id of the seat
# that scaffolded it}}. Only the person sets it; no agent tool does.
_SWARM_TRUST_FILE = "swarm_draft_trust.json"


def _swarm_trust() -> dict[str, Any]:
    try:
        data = json.loads((appdata_dir() / _SWARM_TRUST_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    data = data if isinstance(data, dict) else {}
    return {"groups": [str(g) for g in data.get("groups") or []],
            "drafts": {str(k): str(v) for k, v in (data.get("drafts") or {}).items()}}


def _save_swarm_trust(data: dict[str, Any]) -> None:
    path = appdata_dir() / _SWARM_TRUST_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def set_swarm_draft_trust(group_ids: list[str], on: bool) -> dict[str, Any]:
    """The Build card's switch: ``group_ids`` are every group of one swarm (nested ones
    too). On, ducky_plugin_test from those groups' seats turns on drafts those seats
    scaffolded without the needs_trust stop."""
    ids = {str(g).strip() for g in group_ids or [] if str(g).strip()}
    data = _swarm_trust()
    groups = set(data["groups"]) | ids if on else set(data["groups"]) - ids
    data["groups"] = sorted(groups)
    if not on:
        data["drafts"] = {pid: g for pid, g in data["drafts"].items() if g not in ids}
    _save_swarm_trust(data)
    return {"ok": True, "groups": data["groups"]}


def write_ai_draft_bytes(plugin_id: str, rel: str, data: bytes) -> None:
    """One file of an AI plugin draft (AppData, not a project: no write pipeline),
    exactly these bytes. ``rel`` must stay inside the draft."""
    root = (appdata_ai_plugins_dir() / normalize_plugin_id(plugin_id)).resolve()
    target = (root / rel).resolve()
    if not target.is_relative_to(root) or target == root:
        raise ValueError(f"path escapes draft: {rel!r}")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)


_DRAFT_LINKS_FILE = ".repo_links.json"  # beside the drafts: a file, so draft listings skip it


def ai_draft_repo_links() -> dict[str, str]:
    """plugin id → the repo folder its draft is synced from (ducky_plugin_test repo=)."""
    try:
        data = json.loads((appdata_ai_plugins_dir() / _DRAFT_LINKS_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {str(k): str(v) for k, v in data.items()} if isinstance(data, dict) else {}


def link_ai_draft_repo(plugin_id: str, repo: str) -> None:
    links = {**ai_draft_repo_links(), normalize_plugin_id(plugin_id): str(repo)}
    path = appdata_ai_plugins_dir() / _DRAFT_LINKS_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(links, indent=2), encoding="utf-8")


def _caller_group() -> str:
    try:
        from backend.workspace.identity import resolve_context

        ctx = resolve_context()
    except Exception:  # noqa: BLE001
        return ""
    return str(ctx.group_id or "") if ctx is not None else ""


def note_draft_creator(plugin_id: str) -> None:
    """A draft was scaffolded: remember the caller's group, if it is a swarm seat's."""
    group = _caller_group()
    if group:
        data = _swarm_trust()
        data["drafts"][normalize_plugin_id(plugin_id)] = group
        _save_swarm_trust(data)


def swarm_trusts_draft(plugin_id: str) -> bool:
    """Is the caller a seat of a swarm with the grant, and was the draft scaffolded by
    a seat of that grant?"""
    group = _caller_group()
    if not group:
        return False
    data = _swarm_trust()
    groups = set(data["groups"])
    return group in groups and data["drafts"].get(normalize_plugin_id(plugin_id)) in groups


def _defer_chat_enable_side_effects(plugin_id: str, *, enabled: bool) -> None:
    """Opt-in / prompt-cache wipe across all chats — disk-heavy; run off the UI thread."""
    import threading

    def _work() -> None:
        try:
            from frontend.ui_web.project_chats import (
                invalidate_all_projects_conversation_caches,
                opt_in_uefn_plugin_all_chats,
            )

            # Chat allowlists are not wiped on disable (preference stays); tools
            # gate on Store enable. On enable, frozen allowlists get the new id.
            if enabled:
                opt_in_uefn_plugin_all_chats(plugin_id)
            invalidate_all_projects_conversation_caches()
        except Exception:
            pass

    threading.Thread(
        target=_work,
        name=f"uefn-plugin-chat-{plugin_id}",
        daemon=True,
    ).start()


def set_uefn_plugin_enabled(
    plugin_id: str, enabled: bool, *, trust_local: bool = False, swarm_test: bool = False
) -> dict[str, Any]:
    """``swarm_test``: ducky_plugin_test turning on a draft under the swarm grant
    (:func:`swarm_trusts_draft`, checked here); the plugin isn't added to the trusted list."""
    pid = normalize_plugin_id(plugin_id)
    manifest = load_plugin_manifest(pid)
    if manifest is None:
        raise FileNotFoundError(f"UEFN plugin not found: {pid}")
    source = str(manifest.get("source") or "local")
    if (
        enabled
        and source in _TRUST_REQUIRED_SOURCES
        and not trust_local
        and pid not in _trusted_local_ids()
        and not (swarm_test and source == "ai" and swarm_trusts_draft(pid))
    ):
        kind = "AI-made" if source == "ai" else "local"
        return {
            "ok": False,
            "needs_trust": True,
            "plugin_id": pid,
            "source": source,
            "error": f"Unofficial {kind} plugin — confirm to enable (runs with app permissions).",
        }
    if enabled and source in _TRUST_REQUIRED_SOURCES and trust_local:
        trust_local_plugin(pid)

    from frontend.settings import PanelSettings, replace

    settings = PanelSettings.load()
    current = list(getattr(settings, "enabled_uefn_plugins", None) or [])
    if enabled:
        if pid not in current:
            current.append(pid)
    else:
        current = [p for p in current if p != pid]
    settings = replace(settings, enabled_uefn_plugins=current)
    settings.save()
    # Single-plugin apply — never full reload_plugins() (that re-registers every
    # enabled plugin, e.g. Blender addon deploy, and makes Store toggles crawl).
    from backend.uefn_plugins.host import apply_plugin_enabled_change, clear_plugin_crash

    if enabled:
        clear_plugin_crash(pid)  # turning it on again means: try loading it again
    apply_plugin_enabled_change(pid, enabled=bool(enabled))
    # Drop in-memory skill prompt cache so enable gates flip immediately.
    # No IDE folder sync — pack files on disk don't change on toggle.
    try:
        from backend.agent.prompt import clear_skill_cache

        clear_skill_cache()
    except Exception:
        pass
    _defer_chat_enable_side_effects(pid, enabled=bool(enabled))
    return {"ok": True, "plugin_id": pid, "enabled": enabled, "enabled_uefn_plugins": current}


_SECRET_KEY_LABELS: dict[str, str] = {
    "duckyos_account": "DuckyOS sign-in / device key",
    "discord": "Discord bot token",
    "discord_guild": "Discord server id",
    "discord_name": "Discord bot display name",
    "discord_allowed_ids": "Discord allowed user ids",
    "discord_channel": "Discord default channel",
}


def secret_key_label(key: str) -> str:
    k = (key or "").strip()
    if not k:
        return ""
    known = _SECRET_KEY_LABELS.get(k)
    if known:
        return known
    # Title-case fallback: "api_key" → "Api key"
    return k.replace("_", " ").replace("-", " ").strip().capitalize()


def get_uefn_plugin_secret_labels(plugin_id: str) -> dict[str, Any]:
    """Human labels for secret_keys on an installed plugin (for uninstall confirm)."""
    pid = normalize_plugin_id(plugin_id)
    manifest = load_plugin_manifest(pid)
    if manifest is None:
        raise FileNotFoundError(f"UEFN plugin not found: {pid}")
    raw = manifest.get("secret_keys") if isinstance(manifest.get("secret_keys"), list) else []
    keys = [str(k).strip() for k in raw if str(k).strip()]
    return {
        "ok": True,
        "plugin_id": pid,
        "secret_keys": keys,
        "labels": [secret_key_label(k) for k in keys],
    }


def list_plugin_skill_dirs(plugin_root: Path) -> list[Path]:
    """Skill pack folders under ``skills/<id>/SKILL.md`` inside a plugin tree."""
    root = Path(plugin_root) / PLUGIN_SKILLS_DIR
    if not root.is_dir():
        return []
    out: list[Path] = []
    for child in sorted(root.iterdir()):
        if child.is_dir() and (child / PLUGIN_SKILL_FILE).is_file():
            out.append(child)
    return out


def _plugin_tree_fingerprint(root: Path) -> tuple[tuple[str, int], ...]:
    """Cheap self-invalidating key: (name, mtime_ns) per plugin dir.

    Re-stat'ing ~30 dirs is ~6ms here, and skill deploy probes this hundreds of
    times per run — so the probe itself is memoized for ``_FINGERPRINT_TTL``.
    Install/enable paths call ``invalidate_plugin_skill_caches()`` directly.
    """
    global _FINGERPRINT_AT, _FINGERPRINT, _FINGERPRINT_ROOT
    key = str(root)
    now = time.monotonic()
    with _OWNED_SKILLS_LOCK:
        fresh = _FINGERPRINT is not None and (now - _FINGERPRINT_AT) < _FINGERPRINT_TTL
        if fresh and _FINGERPRINT_ROOT == key:
            return _FINGERPRINT
    try:
        fingerprint = tuple(
            sorted((c.name, c.stat().st_mtime_ns) for c in root.iterdir() if c.is_dir())
        )
    except OSError:
        fingerprint = ()
    with _OWNED_SKILLS_LOCK:
        _FINGERPRINT = fingerprint
        _FINGERPRINT_ROOT = key
        _FINGERPRINT_AT = now
    return fingerprint


def invalidate_plugin_skill_caches() -> None:
    """Drop plugin-owned-skill caches after install / uninstall / enable changes."""
    global _FINGERPRINT, _FINGERPRINT_AT, _FINGERPRINT_ROOT
    with _OWNED_SKILLS_LOCK:
        _FINGERPRINT = None
        _FINGERPRINT_ROOT = ""
        _FINGERPRINT_AT = 0.0
        _OWNED_SKILLS_CACHE.clear()
    try:
        from backend.skills.store import (
            _OWNED_MAP_CACHE,
            _OWNED_MAP_LOCK,
            invalidate_skills_revision,
        )

        with _OWNED_MAP_LOCK:
            _OWNED_MAP_CACHE.clear()
        # Open chats compare this revision to decide whether their frozen skill
        # index needs rebuilding — drop it with the rest.
        invalidate_skills_revision()
    except Exception:
        pass
    try:
        from backend.search import indexer

        indexer.request_refresh("plugins")
    except Exception:
        pass


def list_plugin_owned_skills() -> list[dict[str, str]]:
    """Installed plugin-owned skills: ``{pack_id, path, plugin_id}``.

    Cached on the plugin-tree mtime fingerprint: skill deploy calls this once per
    pack per root (~200x per sync) and the uncached walk re-read every plugin.json
    each time — 6.4s of a 9s deploy. Install/update rewrites the plugin dir, which
    bumps its mtime and drops the entry.
    """
    root = appdata_uefn_plugins_dir()
    if not root.is_dir():
        return []
    fingerprint = _plugin_tree_fingerprint(root)
    with _OWNED_SKILLS_LOCK:
        cached = _OWNED_SKILLS_CACHE.get(fingerprint)
    if cached is not None:
        return [dict(row) for row in cached]
    out: list[dict[str, str]] = []
    for child in sorted(root.iterdir()):
        if not child.is_dir():
            continue
        manifest = _read_json(child / PLUGIN_MANIFEST)
        if not manifest:
            continue
        try:
            pid = normalize_plugin_id(str(manifest.get("id") or child.name))
        except ValueError:
            continue
        for skill_dir in list_plugin_skill_dirs(child):
            out.append(
                {
                    "pack_id": skill_dir.name,
                    "path": str(skill_dir),
                    "plugin_id": pid,
                }
            )
    with _OWNED_SKILLS_LOCK:
        _OWNED_SKILLS_CACHE.clear()  # single entry — only the live tree matters
        _OWNED_SKILLS_CACHE[fingerprint] = [dict(row) for row in out]
    return out


def validate_plugin_skills(
    plugin_root: Path,
    plugin_id: str,
    *,
    replacing: tuple[list[str], list[str]] = ([], []),
) -> list[str]:
    """Validate bundled skills under ``skills/``. Returns skill pack ids.

    ``replacing`` is (plugins, standalone packs) this install removes: their skills don't clash.
    Raises ``ValueError`` on path/id/collision problems.
    """
    gone_plugins, gone_packs = replacing
    from backend.skills.store import appdata_skill_packs_dir, normalize_pack_id

    pid = normalize_plugin_id(plugin_id)
    plugin_root = Path(plugin_root).resolve()
    skills_root = (plugin_root / PLUGIN_SKILLS_DIR).resolve()
    if not skills_root.is_dir():
        return []
    try:
        if not skills_root.is_relative_to(plugin_root):
            raise ValueError("skills/ escapes plugin directory")
    except (OSError, ValueError) as exc:
        raise ValueError(f"invalid skills/ path: {exc}") from exc

    owned_elsewhere: dict[str, str] = {}
    for entry in list_plugin_owned_skills():
        other = str(entry.get("plugin_id") or "")
        pack = str(entry.get("pack_id") or "")
        if pack and other and other != pid and other not in gone_plugins:
            owned_elsewhere[pack] = other

    standalone = appdata_skill_packs_dir()
    pack_ids: list[str] = []
    for skill_dir in list_plugin_skill_dirs(plugin_root):
        try:
            skill_resolved = skill_dir.resolve()
            if not skill_resolved.is_relative_to(skills_root):
                raise ValueError(f"skill path escapes skills/: {skill_dir.name}")
        except (OSError, ValueError) as exc:
            raise ValueError(str(exc)) from exc
        try:
            pack_id = normalize_pack_id(skill_dir.name)
        except ValueError as exc:
            raise ValueError(f"invalid bundled skill id {skill_dir.name!r}: {exc}") from exc
        if pack_id != skill_dir.name:
            raise ValueError(
                f"bundled skill folder {skill_dir.name!r} must equal normalized id {pack_id!r}"
            )
        # Frontmatter name should match folder when present.
        try:
            from backend.skills.store import parse_frontmatter

            raw = (skill_dir / PLUGIN_SKILL_FILE).read_text(encoding="utf-8")
            meta, _ = parse_frontmatter(raw)
            fm_name = str(meta.get("name") or "").strip()
            if fm_name:
                try:
                    fm_id = normalize_pack_id(fm_name)
                except ValueError:
                    fm_id = ""
                if fm_id and fm_id != pack_id:
                    raise ValueError(
                        f"skills/{pack_id}/SKILL.md name {fm_name!r} must match folder id"
                    )
        except OSError as exc:
            raise ValueError(f"cannot read skills/{pack_id}/SKILL.md: {exc}") from exc

        if pack_id in owned_elsewhere:
            raise ValueError(
                f"skill {pack_id!r} already owned by plugin {owned_elsewhere[pack_id]!r}"
            )
        standalone_dir = standalone / pack_id
        if pack_id not in gone_packs and standalone_dir.is_dir() and (standalone_dir / PLUGIN_SKILL_FILE).is_file():
            raise ValueError(
                f"skill {pack_id!r} already installed as a standalone skill pack — "
                "uninstall it before installing this plugin"
            )
        pack_ids.append(pack_id)
    return pack_ids


def _refresh_plugin_skills() -> None:
    """Re-deploy IDE skill folders after plugin install/uninstall changes owned skills."""
    with _PLUGIN_TREE_LOCK:
        try:
            from backend.agent.prompt import clear_skill_cache
            from frontend.skill_deploy import sync_skill_all_ides
            from frontend.settings import PanelSettings

            invalidate_plugin_skill_caches()
            clear_skill_cache()
            sync_skill_all_ides(PanelSettings.load().antigravity_config_path)
        except Exception:
            pass


def _skill_refresh_worker() -> None:
    global _SKILL_REFRESH_WANTED
    while True:
        with _SKILL_REFRESH_LOCK:
            if not _SKILL_REFRESH_WANTED:
                return
            _SKILL_REFRESH_WANTED = False
        _refresh_plugin_skills()


def _refresh_plugin_skills_async() -> None:
    """IDE skill sync is disk-heavy — never block this Store bridge call.

    Still serialize against the next plugin's rmtree: the worker takes
    ``_PLUGIN_TREE_LOCK`` so Update All cannot delete modules.md while we copy it.
    Coalesce so N installs share one sync instead of N overlapping walkers.
    """
    global _SKILL_REFRESH_WANTED, _SKILL_REFRESH_THREAD
    with _SKILL_REFRESH_LOCK:
        _SKILL_REFRESH_WANTED = True
        t = _SKILL_REFRESH_THREAD
        if t is not None and t.is_alive():
            return
        _SKILL_REFRESH_THREAD = threading.Thread(
            target=_skill_refresh_worker,
            daemon=True,
            name="uefn-plugin-skills-refresh",
        )
        _SKILL_REFRESH_THREAD.start()


def uninstall_uefn_plugin(plugin_id: str, *, erase_data: bool = False, wait: bool = False) -> dict[str, Any]:
    """Remove a Store plugin (:func:`_uninstall_uefn_plugin`) holding the install lock."""
    from backend.util.file_lock import held_file_lock

    with held_file_lock(PLUGIN_INSTALL_LOCK, PLUGIN_INSTALL_WAIT_S) as got:
        if not got:
            return dict(_INSTALL_BUSY)
        return _uninstall_uefn_plugin(plugin_id, erase_data=erase_data, wait=wait)


def _uninstall_uefn_plugin(plugin_id: str, *, erase_data: bool, wait: bool) -> dict[str, Any]:
    """Remove a Store plugin from disk and return quickly.

    Persistent work (disable + settings + ``rmtree``) runs on the caller thread.
    Runtime teardown (``unload`` / strip contribs / skill redeploy) runs on a
    daemon so a wedged ``register()`` / ``unload()`` cannot stick the UI on
    ``Uninstalling…``. ``wait`` runs it inline: a plugin installed right after takes
    over the same tool names, and a late teardown would strip them again.
    """
    pid = normalize_plugin_id(plugin_id)
    dest = plugin_dir(pid)
    if not dest.is_dir():
        raise FileNotFoundError(f"UEFN plugin not found: {pid}")
    try:
        from frontend import usage_stats

        usage_stats.plugin_removed(pid)
    except Exception:
        pass
    had_skills = bool(list_plugin_skill_dirs(dest))
    erased_keys: list[str] = []
    if erase_data:
        labels = get_uefn_plugin_secret_labels(pid)
        keys = list(labels.get("secret_keys") or [])
        from backend.agent.secrets import clear_key

        for key in keys:
            try:
                clear_key(key)
                erased_keys.append(key)
            except Exception:
                pass
        if "duckyos_account" in keys:
            try:
                from frontend import duckyos_account

                duckyos_account.logout()
            except Exception:
                pass
        # ADR 0003: the plugin's key/value rows (encrypted ones included) go with it.
        try:
            from frontend.ui_web.plugin_host_api import cache_clear

            cache_clear(pid)
        except Exception:
            pass
        # Host data service docs + files on this PC (a shared space's other copies stay theirs).
        try:
            from backend.uefn_plugins.scopes import erase_plugin

            erase_plugin(pid)
        except Exception:
            pass
        try:
            from backend.uefn_plugins.panel_presence import erase_plugin_local

            erase_plugin_local(pid)  # proxies, thumbnails and caches the plugin made
        except Exception:
            pass
    # Persist disabled before deleting files so a restart mid-teardown still shows off.
    set_uefn_plugin_enabled(pid, False)
    # Drop imported modules (bounded unload) *before* rmtree — Windows locks .py files
    # while the module is still in sys.modules.
    from backend.uefn_plugins.host import (
        invalidate_plugin_runtime,
        release_locked_compiled_backends,
        reload_single_plugin,
    )

    try:
        invalidate_plugin_runtime(pid)
    except Exception:
        pass
    # A loaded compiled backend stays locked until Ducky exits: park it in quarantine (swept
    # next launch) so the folder really goes instead of leaving the .pyd behind on its own.
    release_locked_compiled_backends(pid)
    with _PLUGIN_TREE_LOCK:
        _rmtree_retry(dest, ignore_final=True)
        if dest.is_dir():
            time.sleep(0.05)
            _rmtree_retry(dest, ignore_final=True)
    # Folder is gone now; the memoized fingerprint would keep its skills "owned" until TTL.
    invalidate_plugin_skill_caches()
    try:
        from backend.search import indexer

        indexer.forget_plugin(pid)  # its api.search records; packs and tools leave on the refresh
    except Exception:
        pass
    from frontend.settings import PanelSettings, replace

    settings = PanelSettings.load()
    trusted = [p for p in (getattr(settings, "trusted_local_uefn_plugins", None) or []) if p != pid]
    enabled = [p for p in (getattr(settings, "enabled_uefn_plugins", None) or []) if p != pid]
    settings = replace(settings, trusted_local_uefn_plugins=trusted, enabled_uefn_plugins=enabled)
    settings.save()

    def _teardown() -> None:
        def _notify() -> None:
            try:
                from frontend.ui_web.agent_modes import push_ui_event

                push_ui_event({"type": "uefn_plugins_changed"})
            except Exception:
                pass

        try:
            # Folder gone → strip leftovers / detect-cache only; does not re-register others.
            reload_single_plugin(pid)
            if had_skills:
                _refresh_plugin_skills()
        finally:
            _notify()

    if wait:
        _teardown()
    else:
        threading.Thread(
            target=_teardown,
            daemon=True,
            name=f"uefn-plugin-uninstall-{pid}",
        ).start()
    return {
        "ok": True,
        "plugin_id": pid,
        "erase_data": bool(erase_data),
        "erased_keys": erased_keys,
    }


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
    if len(data) > MAX_PLUGIN_ZIP_BYTES:
        raise ValueError(f"zip exceeds max size ({MAX_PLUGIN_ZIP_BYTES} bytes)")
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        integrity = _zip_integrity_error(zf)
        if integrity:
            raise ValueError(integrity)
        names = zf.namelist()
        manifest_name = None
        for name in names:
            # Allow plugin.json at root or one folder deep (discord/plugin.json).
            parts = Path(name).parts
            if parts and parts[-1] == PLUGIN_MANIFEST and len(parts) <= 2 and ".." not in parts:
                manifest_name = name
                break
        if not manifest_name:
            raise ValueError("zip missing plugin.json")
        raw = zf.read(manifest_name).decode("utf-8")
        manifest = json.loads(raw)
        if not isinstance(manifest, dict):
            raise ValueError("plugin.json must be an object")
        normalize_plugin_id(str(manifest.get("id") or ""))
        return manifest


def _reload_in_app_process(pid: str) -> None:
    """An agent's tool server installs in its own process. The app's panels call the
    plugin's RPCs in the app process, so it loads the new copy too; until it does,
    panels keep calling the code that was there before."""
    try:
        from frontend.ui_web import agent_modes

        if not agent_modes._in_bridge_process():
            return
    except Exception:
        return

    def run() -> None:
        try:
            import urllib.request

            from frontend.settings import PANEL_LISTENER_PORT

            url = f"http://127.0.0.1:{PANEL_LISTENER_PORT - 1}/__panel_api/reload_uefn_plugin"
            data = json.dumps({"args": [pid]}).encode("utf-8")
            req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
            urllib.request.urlopen(req, timeout=30).read()
        except Exception:
            logging.getLogger(__name__).debug("app reload of plugin %s failed", pid, exc_info=True)

    threading.Thread(target=run, daemon=True, name=f"plugin-app-reload-{pid}").start()


# ponytail: one-time move. UEFN 1.1 holds these plugins and the NPC AI skill pack. Installing
# it (by hand, or the Store pass, which moves every PC over) removes them only once it has
# landed; a refused or failed install leaves them installed and working.
UEFN_REPLACES: frozenset[str] = frozenset({
    "animation", "leveldesign", "materials", "modeling", "vfx", "scenegraph", "verse",
    "tester", "uefn-physics", "uefn-virtualpointer", "metahuman", "uefn-logs",
})
UEFN_REPLACES_SKILL_PACKS: frozenset[str] = frozenset({"npc-ai"})


def uefn_takes_over(plugin_id: str, version: object) -> tuple[list[str], list[str]]:
    """(installed Store plugins, standalone skill packs) an install of this version replaces."""
    from backend.skills.store import appdata_skill_packs_dir
    from backend.uefn_plugins.plugin_version import plugin_version_newer

    if plugin_id != "uefn" or plugin_version_newer("1.1.0", version):
        return [], []
    plugins = sorted(p for p in UEFN_REPLACES if plugin_dir(p).is_dir())
    packs = sorted(s for s in UEFN_REPLACES_SKILL_PACKS if (appdata_skill_packs_dir() / s).is_dir())
    return plugins, packs


def uefn_parts_without_uefn() -> bool:
    """True when an old UEFN part (plugin or standalone skill pack) is here but UEFN is not
    (a UEFN folder without its plugin.json is an install that stopped halfway, not UEFN)."""
    return load_plugin_manifest("uefn") is None and any(uefn_takes_over("uefn", "1.1.0"))


def install_stopped_halfway(plugin_id: str) -> bool:
    """True when a plugin is still on but has no plugin.json: an install stopped halfway
    (older versions could, after removing what it replaced), and the folder it left may
    since have been swept at launch. An uninstall turns the plugin off first, so it never counts."""
    try:
        pid = normalize_plugin_id(plugin_id)
    except ValueError:
        return False
    return load_plugin_manifest(pid) is None and pid in get_enabled_plugin_ids()


# One install or uninstall at a time across every Ducky process: the app and the MCP bridge
# each installed UEFN at once, and each removed the old parts the other was replacing.
PLUGIN_INSTALL_LOCK = "plugin-installs"
PLUGIN_INSTALL_WAIT_S = 120.0
_INSTALL_BUSY = {
    "ok": False,
    "code": "install_busy",
    "error": "Another plugin install is still running. Try again once it's done.",
}


def import_plugin_from_bytes(
    data: bytes,
    *,
    source: str = "store",
    replace: bool = True,
    signed_record: dict[str, Any] | None = None,
    replace_old: bool = False,
) -> dict[str, Any]:
    """Install a plugin zip (:func:`_import_plugin_from_bytes`) holding the install lock."""
    from backend.util.file_lock import held_file_lock

    with held_file_lock(PLUGIN_INSTALL_LOCK, PLUGIN_INSTALL_WAIT_S) as got:
        if not got:
            return dict(_INSTALL_BUSY)
        return _import_plugin_from_bytes(
            data, source=source, replace=replace, signed_record=signed_record, replace_old=replace_old
        )


def _import_plugin_from_bytes(
    data: bytes,
    *,
    source: str,
    replace: bool,
    signed_record: dict[str, Any] | None,
    replace_old: bool,
) -> dict[str, Any]:
    """Install a plugin zip into AppData. Secrets never live in the zip.

    Corrupt / Bad-CRC zips are refused **before** any AppData write. A failed
    install never leaves a half-installed plugin folder.

    ``signed_record`` is the Store's signed version record ({…fields, signature}).
    When present it is verified against the zip before any write and kept next to the
    installed plugin for the load-time license check; a tampered record is refused.

    ``replace_old`` lets this install remove the plugins it replaces (``uefn_takes_over``),
    once it has landed; without it such an install is refused and nothing changes.
    """
    if len(data) > MAX_PLUGIN_ZIP_BYTES:
        return {"ok": False, "error": f"zip exceeds max size ({MAX_PLUGIN_ZIP_BYTES} bytes)"}
    try:
        manifest = parse_plugin_manifest_from_zip(data)
    except (ValueError, json.JSONDecodeError, KeyError) as exc:
        return {"ok": False, "error": str(exc)}
    try:
        pid = normalize_plugin_id(str(manifest.get("id") or ""))
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}
    too_old = app_too_old_for(manifest)
    if too_old:
        # Nothing is written: the installed version (if any) keeps working.
        return {"ok": False, "code": "needs_newer_app", "error": too_old}
    # A bad "data_locks" would lock the wrong items, or none, a bad "data_local" would share
    # this PC's own items, and a bad "team_folders" would move the wrong folder: refused
    # like a bad zip. ("data_sharing" was read loosely before it was checked: only the lint
    # names it.)
    bad_locks = [e for e in manifest_data_errors(manifest)
                 if '"data_locks"' in e or '"data_local"' in e or '"team_folders"' in e]
    if bad_locks:
        return {"ok": False, "error": " ".join(bad_locks)}
    from backend.uefn_plugins import asset_packs

    try:
        asset_packs.manifest_packs(manifest)
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}

    # Refuse a Store zip whose signature does not match it (tamper check before write).
    try:
        import hashlib

        from backend.uefn_plugins import signing

        ok, reason = signing.verify_download(signed_record, hashlib.sha256(data).hexdigest())
        if not ok:
            return {"ok": False, "error": reason}
    except Exception:
        pass

    dest = plugin_dir(pid)
    existing = _read_json(dest / PLUGIN_MANIFEST) if dest.is_dir() else None
    existing_source = str(existing.get("source") or "") if existing else ""
    # Same-source replace only — store/local/ai/bundled cannot overwrite each other. A copy
    # that never recorded where it came from (installed before sources were kept) is not a
    # local one: the Store showed it an Update that then always failed, so it never updated.
    if existing and existing_source and existing_source != source:
        return {
            "ok": False,
            "error": (
                f"Plugin {pid} already installed from {existing_source!r} — "
                f"uninstall it before installing from {source!r}"
            ),
        }
    if dest.is_dir() and not replace:
        return {"ok": False, "error": f"Plugin {pid} already installed"}

    # Safe extract: decompress every member FIRST so a corrupt entry never leaves
    # a half-installed plugin folder (Store zips can arrive bit-flipped).
    import zlib

    planned: list[tuple[tuple[str, ...], bytes]] = []
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            integrity = _zip_integrity_error(zf)
            if integrity:
                return {"ok": False, "error": integrity}
            total_uncompressed = sum(int(i.file_size) for i in zf.infolist() if not i.is_dir())
            if total_uncompressed > MAX_PLUGIN_UNCOMPRESSED_BYTES:
                return {
                    "ok": False,
                    "error": f"zip expands to {total_uncompressed} bytes "
                    f"(max {MAX_PLUGIN_UNCOMPRESSED_BYTES}) — refusing to extract",
                }
            tops = {Path(n).parts[0] for n in zf.namelist() if n and not n.endswith("/")}
            strip_root = None
            if len(tops) == 1:
                only = next(iter(tops))
                if (only + "/" + PLUGIN_MANIFEST) in zf.namelist() or any(
                    Path(n).name == PLUGIN_MANIFEST and Path(n).parts[0] == only for n in zf.namelist()
                ):
                    strip_root = only

            for info in zf.infolist():
                if info.is_dir():
                    continue
                parts = Path(info.filename).parts
                if not parts or ".." in parts or Path(info.filename).is_absolute():
                    continue
                rel_parts = parts[1:] if strip_root and parts[0] == strip_root else parts
                if not rel_parts:
                    continue
                try:
                    payload = zf.read(info.filename)
                except (zipfile.BadZipFile, zlib.error, RuntimeError, OSError) as exc:
                    return {
                        "ok": False,
                        "error": f"Corrupt zip entry {info.filename}: {exc}",
                    }
                except Exception as exc:  # noqa: BLE001 — never install a suspect member
                    msg = str(exc)
                    if "CRC" in msg.upper() or "corrupt" in msg.lower():
                        return {
                            "ok": False,
                            "error": f"Corrupt zip entry {info.filename}: {exc}",
                        }
                    raise
                planned.append((tuple(rel_parts), payload))
    except zipfile.BadZipFile as exc:
        return {"ok": False, "error": f"Invalid zip: {exc}"}

    if not planned:
        return {"ok": False, "error": "zip has no installable files"}

    replacing = uefn_takes_over(pid, manifest.get("version"))
    if (replacing[0] or replacing[1]) and not replace_old:
        return {
            "ok": False,
            "code": "replaces_plugins",
            "error": (
                f"{manifest.get('label') or pid} {manifest.get('version')} replaces "
                f"{', '.join(replacing[0] + replacing[1])}. Install it from Settings → Store to "
                "switch over; until then they stay installed."
            ),
        }
    # Check the bundled skills before anything installed is touched: a refused install
    # used to delete the old copy first and leave the plugin gone.
    import tempfile

    try:
        with tempfile.TemporaryDirectory(prefix="ducky-plugin-skills-") as tmp:
            root = Path(tmp)
            for rel_parts, payload in planned:
                if rel_parts[0] != PLUGIN_SKILLS_DIR or ".." in rel_parts:
                    continue
                target = root.joinpath(*rel_parts)
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(payload)
            skill_ids = validate_plugin_skills(root, pid, replacing=replacing)
    except (OSError, ValueError) as exc:
        return {"ok": False, "error": str(exc)}

    # Write the new version into a folder of its own first, in the plugins folder's .staging
    # (one level down, so nothing lists it; the same drive, so it renames in). A write that
    # fails leaves the installed copy, and the plugins this one replaces, as they were.
    # ponytail: a crash mid-install leaves its folder in .staging; sweep it at launch if one shows up.
    try:
        staging_root = appdata_uefn_plugins_dir() / ".staging"
        staging_root.mkdir(parents=True, exist_ok=True)
        staging = Path(tempfile.mkdtemp(prefix=f"plugin-{pid}-", dir=staging_root))
    except OSError as exc:
        return {"ok": False, "error": f"Install failed: {exc}"}
    try:
        wrote_manifest = False
        for rel_parts, payload in planned:
            target = staging.joinpath(*rel_parts)
            try:
                if not target.resolve().is_relative_to(staging.resolve()):
                    continue  # zip-slip / absolute — skip member, do not abort
            except (OSError, ValueError):
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            _write_bytes_retry(target, payload)
            if rel_parts[-1] == PLUGIN_MANIFEST and len(rel_parts) == 1:
                wrote_manifest = True
        if not wrote_manifest and not (staging / PLUGIN_MANIFEST).is_file():
            raise OSError("plugin.json missing after extract")
        written = _read_json(staging / PLUGIN_MANIFEST) or dict(manifest)
        written["id"] = pid
        written["kind"] = str(written.get("kind") or "plugin")
        written["source"] = source
        if isinstance(signed_record, dict) and str(signed_record.get("slug") or "").strip():
            written["store_slug"] = str(signed_record["slug"]).strip()  # asset packs are fetched by it
        # Record a private item's audience, so it can be paused when access to it is
        # lost (and resumed when it returns).
        if isinstance(signed_record, dict):
            from backend.uefn_plugins.signing import AUDIENCE_FIELD

            vis = str(signed_record.get("visibility") or "").strip()
            audience = str(signed_record.get(AUDIENCE_FIELD) or "").strip()
            if vis:
                written["visibility"] = vis
            if vis not in ("", "public") and audience:
                written["audience"] = audience
        _write_json(staging / PLUGIN_MANIFEST, written)
        # Keep the signed record beside the plugin for the load-time license check.
        if isinstance(signed_record, dict) and signed_record.get("signature"):
            try:
                from backend.uefn_plugins import signing

                signing.write_installed_record(staging, signed_record)
            except Exception:
                pass
    except OSError as exc:
        shutil.rmtree(staging, ignore_errors=True)
        return {"ok": False, "error": f"Install failed: {exc}"}
    except ValueError as exc:
        shutil.rmtree(staging, ignore_errors=True)
        return {"ok": False, "error": str(exc)}

    # A compiled backend keeps its .pyd mapped in-process (Windows locks the file),
    # which would block moving the installed folder aside. Move any locked .pyd out
    # first; the new version installs under its own name and loads fresh.
    try:
        from backend.uefn_plugins.host import release_locked_compiled_backends

        stuck = release_locked_compiled_backends(pid)
    except Exception:
        stuck = []
    if stuck:
        # The folder can't move with the file locked in it; stop, old version still running.
        shutil.rmtree(staging, ignore_errors=True)
        return {
            "ok": False,
            "error": (
                f"{manifest.get('label') or pid} is in use and can't be updated right now. "
                "Close and reopen Ducky to finish the update."
            ),
            "code": "plugin_in_use",
        }
    # Drop stale register() bindings / imported modules before overwrite.
    try:
        from backend.uefn_plugins.host import invalidate_plugin_runtime

        invalidate_plugin_runtime(pid)
    except Exception:
        pass

    # Swap it in, holding the tree lock so a sibling Update All / skill-sync cannot keep
    # modules.md open meanwhile (WinError 32). The installed folder moves aside whole (a
    # file held open refuses the rename and nothing changes), then the new one moves in.
    old = staging.with_name(staging.name + "-old")
    try:
        with _PLUGIN_TREE_LOCK:
            if dest.exists():
                _rename_retry(dest, old)
            try:
                _rename_retry(staging, dest)
            except OSError:
                if old.exists():
                    _rename_retry(old, dest)
                raise
    except OSError as exc:
        shutil.rmtree(staging, ignore_errors=True)
        return {"ok": False, "error": f"Install failed: {exc}"}
    shutil.rmtree(old, ignore_errors=True)

    # It has landed: only now do the plugins it replaces go.
    for other in replacing[0]:
        try:
            uninstall_uefn_plugin(other, wait=True)
        except Exception:  # noqa: BLE001 — gone already, or the next UEFN install takes it
            logging.getLogger(__name__).warning("could not remove %s after %s landed", other, pid, exc_info=True)
    if replacing[1]:
        from backend.skills.store import appdata_skill_packs_dir

        for pack in replacing[1]:
            shutil.rmtree(appdata_skill_packs_dir() / pack, ignore_errors=True)
        invalidate_plugin_skill_caches()

    was_installed = existing is not None
    try:  # an uninstall refused this plugin's search records; installed again, it may write them
        from backend.search import indexer

        indexer.plugin_installed(pid)
    except Exception:
        pass
    enabled_now = was_installed and pid in get_enabled_plugin_ids()
    # First Store/bundled install with default_enabled: enable via the real toggle
    # path (settings + contributions). Updates keep the prior on/off state.
    if (
        not was_installed
        and source in ("store", "bundled")
        and bool(written.get("default_enabled", True))
    ):
        en = set_uefn_plugin_enabled(pid, True)
        enabled_now = bool(en.get("ok"))
    else:
        # Update / reinstall: reload ONLY this plugin. Full reload_plugins() during
        # Update All races into duplicate header/settings icons and freezes the UI.
        from backend.uefn_plugins.host import reload_single_plugin

        reload_single_plugin(pid)
        enabled_now = pid in get_enabled_plugin_ids()
    if skill_ids:
        _refresh_plugin_skills_async()
    _reload_in_app_process(pid)
    # Big files this version carries download after the zip landed (the plugin asks
    # api.asset_packs() for progress and api.asset_pack_path(id) once ready).
    if source == "store":
        asset_packs.start_install(pid, written, str(written.get("store_slug") or ""))
    return {
        "ok": True,
        "id": pid,
        "version": format_plugin_version(written.get("version")),
        "source": source,
        "path": str(dest),
        "skills": skill_ids,
        "enabled": enabled_now,
    }
