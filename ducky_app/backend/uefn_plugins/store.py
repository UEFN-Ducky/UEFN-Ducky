"""Desktop plugin pack storage under AppData (mirrors skill / mcp_plugins layout)."""

from __future__ import annotations

import io
import json
import re
import shutil
import sys
import threading
import time
import zipfile
from pathlib import Path
from typing import Any

from backend.skills.store import appdata_dir
from backend.uefn_plugins.plugin_version import format_plugin_version, plugin_version_rank

PLUGIN_MANIFEST = "plugin.json"
UEFN_PLUGINS_DIR = "uefn_plugins"
AI_PLUGINS_DIR = "ai_plugins"
PLUGIN_SKILLS_DIR = "skills"
PLUGIN_SKILL_FILE = "SKILL.md"
_PLUGIN_ID_RE = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
# Sources that need the one-time user trust prompt on first enable.
_TRUST_REQUIRED_SOURCES = frozenset({"local", "ai"})
# ponytail: 32MB covers Discord-sized UI bundles; bump if a plugin ships heavy assets.
MAX_PLUGIN_ZIP_BYTES = 32 * 1024 * 1024
# Zip-bomb guard: extraction happens at install, before the local-trust prompt.
MAX_PLUGIN_UNCOMPRESSED_BYTES = 4 * MAX_PLUGIN_ZIP_BYTES

# fingerprint -> plugin-owned skill rows (see list_plugin_owned_skills).
_OWNED_SKILLS_CACHE: dict[tuple[tuple[str, int], ...], list[dict[str, str]]] = {}
_OWNED_SKILLS_LOCK = threading.Lock()
_FINGERPRINT: tuple[tuple[str, int], ...] | None = None
_FINGERPRINT_ROOT = ""
_FINGERPRINT_AT = 0.0
_FINGERPRINT_TTL = 1.0


def appdata_uefn_plugins_dir() -> Path:
    return appdata_dir() / UEFN_PLUGINS_DIR


def appdata_ai_plugins_dir() -> Path:
    """Shared per-install draft workspace for AI-authored plugins (not loaded directly)."""
    return appdata_dir() / AI_PLUGINS_DIR


def bundled_uefn_plugins_dir() -> Path | None:
    """Legacy single-dir seed root (frontend/uefn_plugins). Prefer iter_bundled_plugin_dirs()."""
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        p = Path(meipass) / "frontend" / UEFN_PLUGINS_DIR
        if p.is_dir():
            return p
    repo = Path(__file__).resolve().parent.parent.parent / "frontend" / UEFN_PLUGINS_DIR
    if repo.is_dir():
        return repo
    return None


def iter_bundled_plugin_dirs() -> list[Path]:
    """No EXE/repo seeding — plugins install only via Store (import_plugin_from_bytes)."""
    return []


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


def load_plugin_manifest(plugin_id: str) -> dict[str, Any] | None:
    pid = normalize_plugin_id(plugin_id)
    return _read_json(appdata_uefn_plugins_dir() / pid / PLUGIN_MANIFEST)


def plugin_dir(plugin_id: str) -> Path:
    return appdata_uefn_plugins_dir() / normalize_plugin_id(plugin_id)


def _copy_plugin_tree(src: Path, dest: Path) -> None:
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(src, dest)


def seed_uefn_plugins(*, force: bool = False) -> list[str]:
    """Copy bundled plugins into AppData. Never overwrites local (source=local) packs."""
    logs: list[str] = []
    sources = iter_bundled_plugin_dirs()
    if not sources:
        logs.append("Bundled uefn_plugins/ not found")
        return logs
    dest_root = appdata_uefn_plugins_dir()
    dest_root.mkdir(parents=True, exist_ok=True)
    for src_plugin in sources:
        manifest = _read_json(src_plugin / PLUGIN_MANIFEST)
        if not manifest:
            continue
        try:
            plugin_id = normalize_plugin_id(str(manifest.get("id") or src_plugin.name))
        except ValueError:
            continue
        dest_plugin = dest_root / plugin_id
        dest_manifest = _read_json(dest_plugin / PLUGIN_MANIFEST) if dest_plugin.is_dir() else None
        if dest_manifest and str(dest_manifest.get("source") or "") == "local":
            logs.append(f"Skip seed {plugin_id}: local plugin occupies id")
            continue
        bundled_ver = plugin_version_rank(manifest.get("version"))
        dest_ver = plugin_version_rank(dest_manifest.get("version")) if dest_manifest else 0
        if dest_plugin.is_dir() and not force and dest_ver >= bundled_ver:
            logs.append(f"UEFN plugin {plugin_id} v{dest_ver} current")
            continue
        _copy_plugin_tree(src_plugin, dest_plugin)
        # Drop scripts/deploy from standalone packages if copied.
        for junk in ("scripts", "deploy", ".git"):
            junk_path = dest_plugin / junk
            if junk_path.is_dir():
                shutil.rmtree(junk_path, ignore_errors=True)
        seeded = _read_json(dest_plugin / PLUGIN_MANIFEST) or {}
        seeded["source"] = "bundled"
        _write_json(dest_plugin / PLUGIN_MANIFEST, seeded)
        try:
            skill_ids = validate_plugin_skills(dest_plugin, plugin_id)
        except ValueError as exc:
            shutil.rmtree(dest_plugin, ignore_errors=True)
            logs.append(f"UEFN plugin {plugin_id} seed rejected: {exc}")
            continue
        logs.append(f"UEFN plugin {plugin_id} v{bundled_ver} seeded -> {dest_plugin}")
        if skill_ids:
            logs.append(f"UEFN plugin {plugin_id} skills: {', '.join(skill_ids)}")
        _ensure_default_enabled(plugin_id, seeded)
    return logs


def _ensure_default_enabled(plugin_id: str, manifest: dict[str, Any]) -> None:
    """First seed with default_enabled: add to enabled list once."""
    if not bool(manifest.get("default_enabled", True)):
        return
    from frontend.settings import PanelSettings, replace

    settings = PanelSettings.load()
    current = list(getattr(settings, "enabled_uefn_plugins", None) or [])
    # None marker means "never initialized" — treat default_enabled plugins as on.
    raw = getattr(settings, "enabled_uefn_plugins", None)
    if raw is None:
        # Fresh settings field: enable all default_enabled installed plugins.
        enabled = [plugin_id]
        for child in appdata_uefn_plugins_dir().iterdir() if appdata_uefn_plugins_dir().is_dir() else []:
            if not child.is_dir():
                continue
            m = _read_json(child / PLUGIN_MANIFEST)
            if not m:
                continue
            try:
                pid = normalize_plugin_id(str(m.get("id") or child.name))
            except ValueError:
                continue
            if bool(m.get("default_enabled", True)) and pid not in enabled:
                enabled.append(pid)
        settings = replace(settings, enabled_uefn_plugins=enabled)
        settings.save()
        return
    if plugin_id not in current and bool(manifest.get("default_enabled", True)):
        # Only auto-enable on first appearance of this id.
        marker = appdata_uefn_plugins_dir() / plugin_id / ".enabled_once"
        if marker.is_file():
            return
        current.append(plugin_id)
        settings = replace(settings, enabled_uefn_plugins=current)
        settings.save()
        try:
            marker.write_text("1\n", encoding="utf-8")
        except OSError:
            pass


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
    seed_uefn_plugins()
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


def set_uefn_plugin_enabled(plugin_id: str, enabled: bool, *, trust_local: bool = False) -> dict[str, Any]:
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
    from backend.uefn_plugins.host import apply_plugin_enabled_change

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
        from backend.skills.store import _OWNED_MAP_CACHE, _OWNED_MAP_LOCK

        with _OWNED_MAP_LOCK:
            _OWNED_MAP_CACHE.clear()
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


def validate_plugin_skills(plugin_root: Path, plugin_id: str) -> list[str]:
    """Validate bundled skills under ``skills/``. Returns skill pack ids.

    Raises ``ValueError`` on path/id/collision problems.
    """
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
        if pack and other and other != pid:
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
        if standalone_dir.is_dir() and (standalone_dir / PLUGIN_SKILL_FILE).is_file():
            raise ValueError(
                f"skill {pack_id!r} already installed as a standalone skill pack — "
                "uninstall it before installing this plugin"
            )
        pack_ids.append(pack_id)
    return pack_ids


def _refresh_plugin_skills() -> None:
    """Re-deploy IDE skill folders after plugin install/uninstall changes owned skills."""
    try:
        from backend.agent.prompt import clear_skill_cache
        from frontend.skill_deploy import sync_skill_all_ides
        from frontend.settings import PanelSettings

        invalidate_plugin_skill_caches()
        clear_skill_cache()
        sync_skill_all_ides(PanelSettings.load().antigravity_config_path)
    except Exception:
        pass


def _refresh_plugin_skills_async() -> None:
    """IDE skill sync is disk-heavy — never block the Store bridge call."""
    threading.Thread(
        target=_refresh_plugin_skills,
        daemon=True,
        name="uefn-plugin-skills-refresh",
    ).start()


def uninstall_uefn_plugin(plugin_id: str, *, erase_data: bool = False) -> dict[str, Any]:
    """Remove a Store plugin from disk and return quickly.

    Persistent work (disable + settings + ``rmtree``) runs on the caller thread.
    Runtime teardown (``unload`` / strip contribs / skill redeploy) runs on a
    daemon so a wedged ``register()`` / ``unload()`` cannot stick the UI on
    ``Uninstalling…``.
    """
    pid = normalize_plugin_id(plugin_id)
    dest = plugin_dir(pid)
    if not dest.is_dir():
        raise FileNotFoundError(f"UEFN plugin not found: {pid}")
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
    # Persist disabled before deleting files so a restart mid-teardown still shows off.
    set_uefn_plugin_enabled(pid, False)
    # Drop imported modules (bounded unload) *before* rmtree — Windows locks .py files
    # while the module is still in sys.modules.
    from backend.uefn_plugins.host import invalidate_plugin_runtime, reload_single_plugin

    try:
        invalidate_plugin_runtime(pid)
    except Exception:
        pass
    shutil.rmtree(dest, ignore_errors=True)
    if dest.is_dir():
        # Rare: unload still holding a handle — retry once after a short pause.
        time.sleep(0.05)
        shutil.rmtree(dest, ignore_errors=True)
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


def import_plugin_from_bytes(
    data: bytes,
    *,
    source: str = "store",
    replace: bool = True,
) -> dict[str, Any]:
    """Install a plugin zip into AppData. Secrets never live in the zip.

    Corrupt / Bad-CRC zips are refused **before** any AppData write. A failed
    install never leaves a half-installed plugin folder.
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

    dest = plugin_dir(pid)
    existing = _read_json(dest / PLUGIN_MANIFEST) if dest.is_dir() else None
    existing_source = str(existing.get("source") or "local") if existing else ""
    # Same-source replace only — store/local/ai/bundled cannot overwrite each other.
    if existing and existing_source != source:
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

    # Drop stale register() bindings / imported modules before overwrite.
    try:
        from backend.uefn_plugins.host import invalidate_plugin_runtime

        invalidate_plugin_runtime(pid)
    except Exception:
        pass

    # Only wipe the old install after the new zip fully validated in memory.
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True, exist_ok=True)
    try:
        wrote_manifest = False
        for rel_parts, payload in planned:
            target = dest.joinpath(*rel_parts)
            try:
                if not target.resolve().is_relative_to(dest.resolve()):
                    continue  # zip-slip / absolute — skip member, do not abort
            except (OSError, ValueError):
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(payload)
            if rel_parts[-1] == PLUGIN_MANIFEST and len(rel_parts) == 1:
                wrote_manifest = True
        if not wrote_manifest and not (dest / PLUGIN_MANIFEST).is_file():
            raise OSError("plugin.json missing after extract")
    except OSError as exc:
        if dest.exists():
            shutil.rmtree(dest, ignore_errors=True)
        return {"ok": False, "error": f"Install failed: {exc}"}

    written = _read_json(dest / PLUGIN_MANIFEST) or dict(manifest)
    written["id"] = pid
    written["kind"] = str(written.get("kind") or "plugin")
    written["source"] = source
    _write_json(dest / PLUGIN_MANIFEST, written)
    try:
        skill_ids = validate_plugin_skills(dest, pid)
    except ValueError as exc:
        if dest.exists():
            shutil.rmtree(dest, ignore_errors=True)
        return {"ok": False, "error": str(exc)}

    was_installed = existing is not None
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
    return {
        "ok": True,
        "id": pid,
        "version": format_plugin_version(written.get("version")),
        "source": source,
        "path": str(dest),
        "skills": skill_ids,
        "enabled": enabled_now,
    }
