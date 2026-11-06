"""Load one plugin: its plugin.json contribution rows, then its backend's ``register()``."""

from __future__ import annotations

import re
import threading
import time
from pathlib import Path
from typing import Any

from backend.uefn_plugins.plugin_version import plugin_version_rank
from backend.uefn_plugins.store import PLUGIN_MANIFEST


def backend_module_name(plugin_id: str) -> str:
    """Python module name for a plugin backend: ``uefn_plugin_<id>``.

    Hyphens in the plugin id become underscores so the name is a valid identifier —
    a compiled extension's init symbol (``PyInit_uefn_plugin_<id>``) must be one, and
    source and compiled plugins then share one sys.modules key.
    """
    return "uefn_plugin_" + str(plugin_id or "").replace("-", "_")


def _read_manifest(path: Path) -> dict[str, Any] | None:
    try:
        import json

        data = json.loads((path / PLUGIN_MANIFEST).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except (OSError, json.JSONDecodeError, ValueError):
        return None


_PLUGIN_LOAD_LOCKS: dict[str, threading.RLock] = {}
_PLUGIN_LOAD_LOCKS_GUARD = threading.Lock()


def _plugin_load_lock(pid: str) -> threading.RLock:
    """One loader per plugin at a time. Oct 29 2026: a Store reload and the repair worker both
    loaded Blender; a compiled backend's module init can't run twice at once (access violation
    in python313.dll), and two register() passes duplicated its contributions."""
    with _PLUGIN_LOAD_LOCKS_GUARD:
        return _PLUGIN_LOAD_LOCKS.setdefault(pid, threading.RLock())


def _load_enabled_plugin(pid: str, root: Path, manifest: dict[str, Any]) -> None:
    """Load one enabled plugin. Keep plugin.json UI rows even if register() fails."""
    with _plugin_load_lock(pid):
        _load_enabled_plugin_locked(pid, root, manifest)


def _load_enabled_plugin_locked(pid: str, root: Path, manifest: dict[str, Any]) -> None:
    try:
        _load_one(pid, root, manifest)
    except Exception as exc:  # noqa: BLE001 — never kill app startup
        _log.exception("UEFN plugin %s failed to load: %s", pid, exc)
        _record_plugin_load_error(pid, exc)
        try:
            invalidate_plugin_runtime(pid)
        except Exception:
            pass
        # ponytail: leave llm_providers / coding_agents / ide.hookups so LLMs
        # still lists the gateway; repair retries register() without wiping UI.


def _warm_plugin_backend(pid: str, root: Path, manifest: dict[str, Any]) -> None:
    """Pre-import a source backend (slow). Safe across threads — unique module names.

    Compiled backends are left to the guarded load: their native init runs one at a
    time there, so a crash names the plugin and the next launch skips it. Pre-importing
    them here crashed Ducky on every launch with nothing recorded.
    """
    if pid in _REGISTERED or _manifest_is_compiled(manifest):
        return
    if lazy_eligible(pid, root, manifest) is not None:
        return  # imported on its first tool call or panel RPC
    version = str(manifest.get("version") or "")
    if plugin_crashed_ducky(pid, version):
        return
    from backend.uefn_plugins.store import app_too_old_for

    if app_too_old_for(manifest):
        return
    backend = manifest.get("backend") if isinstance(manifest.get("backend"), dict) else {}
    entry = str(backend.get("entry") or "backend").strip() or "backend"
    marker = _load_marker_begin(pid, version)
    try:
        _import_backend(pid, root, entry)
    except Exception:  # noqa: BLE001 — register path records the real failure
        _log.debug("UEFN plugin %s pre-import failed", pid, exc_info=True)
    finally:
        _load_marker_end(marker)


# A single plugin's register() must never keep the whole app in "plugins
# loading" — that state hides gateways, models, MCP rows and Store toggles.
# Offenders block on the network: unity-mcp nested MCP sync (45s per server),
# Discord gateway connect, Blender socket probe.
_REGISTER_TIMEOUT_SEC = 20.0


def _register_plugin_guarded(pid: str, root: Path, manifest: dict[str, Any]) -> bool:
    """Run one register() on a watchdog thread. False when it overran the timeout.

    The thread keeps running past the timeout — contributions land later and the
    panel picks them up through the usual plugins-changed notify.
    """
    t0 = time.perf_counter()
    done = threading.Event()

    def _run() -> None:
        _LOAD_THREAD_STATE.in_load = True
        try:
            _load_enabled_plugin(pid, root, manifest)
        finally:
            done.set()

    threading.Thread(
        target=_run,
        daemon=True,
        name=f"uefn-plugin-register-{pid}",
    ).start()
    if not done.wait(_REGISTER_TIMEOUT_SEC):
        _log.warning(
            "UEFN plugin %s register() still running after %.0fs — continuing without it",
            pid,
            _REGISTER_TIMEOUT_SEC,
        )

        def _late_notify() -> None:
            done.wait()
            _notify_uefn_plugins_changed()

        threading.Thread(
            target=_late_notify,
            daemon=True,
            name=f"uefn-plugin-register-late-{pid}",
        ).start()
        return False
    _log.info(
        "UEFN plugin %s register() done in %.0fms",
        pid,
        (time.perf_counter() - t0) * 1000.0,
    )
    return True


def _load_one(pid: str, root: Path, manifest: dict[str, Any], *, register: bool = True) -> None:
    contributes = manifest.get("contributes") if isinstance(manifest.get("contributes"), dict) else {}
    limit = manifest_tool_concurrency(manifest)
    with _LOCK:
        if _TOOL_CONCURRENCY.get(pid) != limit:
            _TOOL_CONCURRENCY[pid] = limit
            _TOOL_SLOTS.pop(pid, None)  # calls still running release the old semaphore
    # Refresh this plugin's contrib rows (safe to re-run after a failed register).
    _strip_contributions_for(pid)

    for tab in contributes.get("settings.tabs") or contributes.get("settings_tabs") or []:
        if isinstance(tab, dict) and tab.get("id"):
            entry = dict(tab)
            entry["plugin_id"] = pid
            resolved = _resolve_contrib_icon(entry.get("icon"), root)
            if resolved:
                entry["icon"] = resolved
            elif "icon" in entry and str(entry.get("icon") or "").startswith("assets/"):
                entry.pop("icon", None)
            _CONTRIBUTIONS["settings_tabs"].append(entry)

    for section in contributes.get("settings.sections") or contributes.get("settings_sections") or []:
        if isinstance(section, dict) and section.get("id") and section.get("tab"):
            props = section.get("properties")
            if not isinstance(props, list) or not props:
                continue
            entry = dict(section)
            entry["plugin_id"] = pid
            entry["properties"] = [dict(p) for p in props if isinstance(p, dict) and p.get("id")]
            if entry["properties"]:
                _CONTRIBUTIONS["settings_sections"].append(entry)

    for panel in contributes.get("dock.panels") or contributes.get("dock_panels") or []:
        if isinstance(panel, dict) and panel.get("id"):
            entry = dict(panel)
            entry["plugin_id"] = pid
            # Appearance → Sidebar shows it next to the panel: its own icon, else the plugin's.
            resolved = _resolve_contrib_icon(entry.get("icon"), root)
            if resolved:
                entry["icon"] = resolved
            else:
                entry.pop("icon", None)
            _CONTRIBUTIONS["dock_panels"].append(entry)

    for kind in contributes.get("editor.kinds") or contributes.get("editor_kinds") or []:
        if isinstance(kind, dict) and kind.get("kind"):
            entry = dict(kind)
            entry["plugin_id"] = pid
            _CONTRIBUTIONS["editor_kinds"].append(entry)

    for btn in contributes.get("header.buttons") or contributes.get("header_buttons") or []:
        if isinstance(btn, dict) and btn.get("id") and btn.get("action"):
            entry = dict(btn)
            entry["plugin_id"] = pid
            resolved = _resolve_contrib_icon(entry.get("icon"), root)
            if resolved:
                entry["icon"] = resolved
            elif "icon" in entry and str(entry.get("icon") or "").startswith("assets/"):
                entry.pop("icon", None)
            _CONTRIBUTIONS["header_buttons"].append(entry)

    # A title-bar button whose dropdown is one of the plugin's own ui.panels pages.
    for menu in contributes.get("header.menus") or contributes.get("header_menus") or []:
        if isinstance(menu, dict) and menu.get("id") and str(menu.get("ui") or "").startswith("panel:"):
            entry = dict(menu)
            entry["plugin_id"] = pid
            resolved = _resolve_contrib_icon(entry.get("icon"), root)
            if resolved:
                entry["icon"] = resolved
            elif str(entry.get("icon") or "").startswith("assets/"):
                entry.pop("icon", None)
            _CONTRIBUTIONS["header_menus"].append(entry)

    chat = contributes.get("chat") if isinstance(contributes.get("chat"), dict) else {}
    ref_rows = chat.get("references") if isinstance(chat.get("references"), list) else None
    if ref_rows is None:
        raw_refs = contributes.get("chat_references")
        ref_rows = raw_refs if isinstance(raw_refs, list) else []
    for row in ref_rows:
        entry = normalize_chat_reference(row, pid) if isinstance(row, dict) else None
        if entry:
            _CONTRIBUTIONS["chat_references"].append(entry)

    sources = chat.get("reference_sources") if isinstance(chat.get("reference_sources"), list) else None
    if sources is None:
        raw_sources = contributes.get("chat_reference_sources")
        sources = raw_sources if isinstance(raw_sources, list) else []
    for source in sources:
        if isinstance(source, dict) and source.get("id") and source.get("rpc") and source.get("panel"):
            entry = dict(source)
            entry["plugin_id"] = pid
            entry["icon"] = _resolve_contrib_icon(entry.get("icon"), root) or ""
            _CONTRIBUTIONS["reference_sources"].append(entry)

    for source in contributes.get("project.sources") or contributes.get("project_sources") or []:
        if isinstance(source, dict) and source.get("id") and source.get("rpc"):
            entry = dict(source)
            entry["plugin_id"] = pid
            resolved = _resolve_contrib_icon(entry.get("icon"), root)
            if resolved:
                entry["icon"] = resolved
            else:
                entry.pop("icon", None)
            _CONTRIBUTIONS["project_sources"].append(entry)

    wt = contributes.get("walkthrough")
    if isinstance(wt, dict) and isinstance(wt.get("steps"), list) and wt.get("steps"):
        entry = dict(wt)
        entry["plugin_id"] = pid
        if not entry.get("id"):
            entry["id"] = pid
        _CONTRIBUTIONS["walkthroughs"].append(entry)

    # Plugin webview panels (Phase 2) — logic lives in webview.py.
    from backend.uefn_plugins.webview import merge_ui_panels, sanitize_entry

    ver_raw = manifest.get("version")
    ver = plugin_version_rank(ver_raw) if ver_raw is not None else None
    for row in merge_ui_panels(contributes, pid, root, version=ver):
        # Default panel icon is the word "duck", which the tab bar drew as a ducky.
        # Prefer this plugin's own assets/icon when the panel didn't set one.
        resolved = _resolve_contrib_icon(row.get("icon"), root)
        if resolved:
            row["icon"] = resolved
        _CONTRIBUTIONS["ui_panels"].append(row)

    for boot in contributes.get("shell.boot") or contributes.get("shell_boot") or []:
        if not isinstance(boot, dict):
            continue
        entry = sanitize_entry(str(boot.get("entry") or "").strip(), root)
        if not entry:
            continue
        _CONTRIBUTIONS["shell_boots"].append({"plugin_id": pid, "entry": entry})

    for profile in contributes.get("appearance.profiles") or contributes.get("appearance_profiles") or []:
        if not isinstance(profile, dict):
            continue
        local_id = str(profile.get("id") or "").strip()
        name = str(profile.get("name") or "").strip()
        if not local_id or not name:
            continue
        foundation = profile.get("foundation") if isinstance(profile.get("foundation"), dict) else {}
        overrides = profile.get("overrides") if isinstance(profile.get("overrides"), dict) else {}
        status = (
            profile.get("status_overrides")
            if isinstance(profile.get("status_overrides"), dict)
            else {}
        )
        _CONTRIBUTIONS["appearance_profiles"].append(
            {
                "id": local_id,
                "name": name,
                "plugin_id": pid,
                "foundation": {str(k): str(v) for k, v in foundation.items() if v},
                "overrides": {str(k): str(v) for k, v in overrides.items() if v},
                "status_overrides": {
                    str(sid): {str(fk): str(fv) for fk, fv in fields.items() if fv}
                    for sid, fields in status.items()
                    if isinstance(fields, dict)
                },
            }
        )

    for css in contributes.get("appearance.css") or contributes.get("appearance_css") or []:
        if not isinstance(css, dict):
            continue
        entry = sanitize_entry(str(css.get("entry") or "").strip(), root)
        if not entry:
            continue
        _CONTRIBUTIONS["appearance_css"].append({"plugin_id": pid, "entry": entry})

    for effect in contributes.get("appearance.effects") or contributes.get("appearance_effects") or []:
        if not isinstance(effect, dict):
            continue
        eid = str(effect.get("id") or "").strip()
        label = str(effect.get("label") or eid).strip()
        entry = sanitize_entry(str(effect.get("entry") or "").strip(), root)
        if not eid or not entry:
            continue
        _CONTRIBUTIONS["appearance_effects"].append(
            {"id": eid, "label": label or eid, "plugin_id": pid, "entry": entry}
        )

    for skin in contributes.get("appearance.skin") or contributes.get("appearance_skins") or []:
        if not isinstance(skin, dict):
            continue
        sid = str(skin.get("id") or "").strip()
        label = str(skin.get("label") or sid).strip()
        entry = sanitize_entry(str(skin.get("entry") or "").strip(), root)
        if not sid or not entry:
            continue
        css_raw = str(skin.get("css") or "").strip()
        css_entry = sanitize_entry(css_raw, root) if css_raw else None
        row: dict[str, Any] = {
            "id": sid,
            "label": label or sid,
            "plugin_id": pid,
            "entry": entry,
        }
        if css_entry:
            row["css"] = css_entry
        _CONTRIBUTIONS["appearance_skins"].append(row)

    for sound in contributes.get("sounds") or []:
        if not isinstance(sound, dict):
            continue
        sid = str(sound.get("id") or "").strip()
        file_entry = sanitize_entry(str(sound.get("file") or "").strip(), root)
        if not sid or not file_entry:
            continue
        label = str(sound.get("label") or sid).strip()
        _CONTRIBUTIONS["sounds"].append(
            {"id": sid, "label": label or sid, "file": file_entry, "plugin_id": pid}
        )

    for tmpl in contributes.get("verse.templates") or contributes.get("verse_templates") or []:
        if not isinstance(tmpl, dict):
            continue
        tid = str(tmpl.get("id") or "").strip()
        name = str(tmpl.get("name") or tid).strip()
        if not tid or not name:
            continue
        icon = str(tmpl.get("icon") or "📄").strip() or "📄"
        description = str(tmpl.get("description") or "").strip()
        folder = str(tmpl.get("folder") or "").strip()
        content = str(tmpl.get("content") or "")
        file_entry: str | None = None
        pack_files: list[dict[str, str]] = []
        raw_files = tmpl.get("files")
        if isinstance(raw_files, list) and raw_files:
            ok_pack = True
            for item in raw_files:
                if not isinstance(item, dict):
                    ok_pack = False
                    break
                rel_path = str(item.get("path") or "").strip().replace("\\", "/")
                src_raw = str(item.get("file") or item.get("content_file") or "").strip()
                inline = str(item.get("content") or "")
                if not rel_path or ".." in rel_path.split("/") or rel_path.startswith("/"):
                    ok_pack = False
                    break
                body = inline
                if src_raw:
                    src_entry = sanitize_entry(src_raw, root)
                    if not src_entry:
                        ok_pack = False
                        break
                    try:
                        body = (root / src_entry).read_text(encoding="utf-8")
                    except OSError:
                        _log.warning(
                            "verse template %s:%s missing pack file %s", pid, tid, src_entry
                        )
                        ok_pack = False
                        break
                # ponytail: 200 KiB per file; upgrade = stream via /plugin-ui if packs grow
                if len(body) > 200_000:
                    _log.warning(
                        "verse template %s:%s file %s too large (%d)",
                        pid,
                        tid,
                        rel_path,
                        len(body),
                    )
                    ok_pack = False
                    break
                pack_files.append({"path": rel_path, "content": body})
            if not ok_pack or not pack_files:
                continue
            if not content and pack_files:
                content = pack_files[0]["content"]
        else:
            file_raw = str(tmpl.get("file") or "").strip()
            if file_raw:
                file_entry = sanitize_entry(file_raw, root)
                if not file_entry:
                    continue
                try:
                    content = (root / file_entry).read_text(encoding="utf-8")
                except OSError:
                    _log.warning("verse template %s:%s missing file %s", pid, tid, file_entry)
                    continue
            if len(content) > 200_000:
                _log.warning(
                    "verse template %s:%s content too large (%d)", pid, tid, len(content)
                )
                continue
        try:
            order = int(tmpl.get("order") if tmpl.get("order") is not None else 100)
        except (TypeError, ValueError):
            order = 100
        connects = tmpl.get("connects") or tmpl.get("connects_to") or []
        if not isinstance(connects, list):
            connects = []
        connects_clean = [str(c).strip() for c in connects if str(c).strip()]
        row: dict[str, Any] = {
            "id": tid,
            "name": name,
            "icon": icon,
            "description": description,
            "content": content,
            "order": order,
            "plugin_id": pid,
        }
        if folder:
            row["folder"] = folder
        if file_entry:
            row["file"] = file_entry
        if pack_files:
            row["files"] = pack_files
        if connects_clean:
            row["connects"] = connects_clean
        _CONTRIBUTIONS["verse_templates"].append(row)

    for hook in contributes.get("hooks") or []:
        if not isinstance(hook, dict):
            continue
        hid = str(hook.get("id") or "").strip()
        if not hid:
            continue
        label = str(hook.get("label") or hid).strip()
        _CONTRIBUTIONS["hooks"].append(
            {"id": hid, "label": label or hid, "plugin_id": pid}
        )

    auto = contributes.get("automations") if isinstance(contributes.get("automations"), dict) else {}
    for row in auto.get("triggers") or []:
        parsed = _automation_contrib_row(row, pid)
        if parsed:
            _CONTRIBUTIONS["automations_triggers"].append(parsed)
    for row in auto.get("nodes") or []:
        parsed = _automation_contrib_row(row, pid)
        if parsed:
            _CONTRIBUTIONS["automations_nodes"].append(parsed)
    for row in auto.get("templates") or []:
        parsed = _automation_template_row(row, pid)
        if parsed:
            _CONTRIBUTIONS["automations_templates"].append(parsed)
    for row in auto.get("image_generators") or []:
        parsed = _image_generator_row(row, pid)
        if parsed:
            _CONTRIBUTIONS["automations_image_generators"].append(parsed)
    for row in auto.get("media_backends") or []:
        parsed = _media_backend_row(row, pid)
        if parsed:
            _CONTRIBUTIONS["automations_media_backends"].append(parsed)

    for voice in contributes.get("tts.voices") or contributes.get("tts_voices") or []:
        if not isinstance(voice, dict):
            continue
        vid = str(voice.get("id") or "").strip()
        if not vid:
            continue
        label = str(voice.get("label") or vid).strip()
        _CONTRIBUTIONS["tts_voices"].append(
            {"id": vid, "label": label or vid, "plugin_id": pid}
        )

    for prov in contributes.get("llm.providers") or contributes.get("llm_providers") or []:
        if not isinstance(prov, dict):
            continue
        provid = str(prov.get("id") or "").strip().lower()
        if not provid:
            continue
        kind = str(prov.get("kind") or "secret").strip().lower() or "secret"
        if kind not in ("secret", "url"):
            kind = "secret"
        try:
            order = int(prov.get("order") if prov.get("order") is not None else 100)
        except (TypeError, ValueError):
            order = 100
        entry = {
            "id": provid,
            "label": str(prov.get("label") or provid).strip() or provid,
            "kind": kind,
            "secret_key": str(prov.get("secret_key") or provid).strip() or provid,
            "default_url": str(prov.get("default_url") or "").strip(),
            "order": order,
            "plugin_id": pid,
            "key_optional": bool(prov.get("key_optional")),
            # Missing chat stays a chat gateway. Explicit false is a key-only row (TypeSafe).
            "chat": prov.get("chat") is not False,
        }
        if "shows_thinking_effort" in prov:
            entry["shows_thinking_effort"] = bool(prov.get("shows_thinking_effort"))
        _CONTRIBUTIONS["llm_providers"].append(entry)

    for agent in contributes.get("llm.coding_agents") or contributes.get("llm_coding_agents") or []:
        if not isinstance(agent, dict):
            continue
        aid = str(agent.get("id") or "").strip().lower().replace("-", "_")
        if not aid:
            continue
        try:
            order = int(agent.get("order") if agent.get("order") is not None else 100)
        except (TypeError, ValueError):
            order = 100
        _CONTRIBUTIONS["llm_coding_agents"].append(
            {
                "id": aid,
                "label": str(agent.get("label") or aid).strip() or aid,
                "order": order,
                "plugin_id": pid,
            }
        )

    for hook in contributes.get("ide.hookups") or contributes.get("ide_hookups") or []:
        if not isinstance(hook, dict):
            continue
        kind = str(hook.get("kind") or "").strip().lower().replace("-", "_")
        if not kind or not kind.replace("_", "").isalnum():
            continue
        _CONTRIBUTIONS["ide_hookups"].append(
            {
                "kind": kind,
                "label": str(hook.get("label") or kind).strip() or kind,
                "plugin_id": pid,
            }
        )

    tools_meta = contributes.get("agent.tools") or contributes.get("agent_tools")
    tool_names: set[str] = set()
    plan_names: set[str] = set()
    destructive_names: set[str] = set()
    declared_tools: set[str] = set()
    # agent.tools.sensitive: true (every tool) or a list of names → model-gated.
    sensitive: bool | list[str] = False
    intent_re = None
    if isinstance(tools_meta, dict):
        raw_sensitive = tools_meta.get("sensitive")
        if raw_sensitive is True:
            sensitive = True
        elif isinstance(raw_sensitive, list):
            sensitive = sorted({n.strip() for n in raw_sensitive if isinstance(n, str) and n.strip()})
        for name in tools_meta.get("tools") or []:
            if isinstance(name, str) and name.strip():
                declared_tools.add(name.strip())
                tool_names.add(name.strip())
        for name in tools_meta.get("plan_tools") or []:
            if isinstance(name, str) and name.strip():
                plan_names.add(name.strip())
        for name in tools_meta.get("destructive_tools") or []:
            if isinstance(name, str) and name.strip():
                destructive_names.add(name.strip())
        raw_pat = tools_meta.get("intent_pattern")
        if isinstance(raw_pat, str) and raw_pat.strip():
            try:
                intent_re = re.compile(raw_pat, re.I)
            except re.error:
                intent_re = None
        category = str(tools_meta.get("category") or pid)
    elif tools_meta is True:
        category = pid
    else:
        category = pid

    # Tools previously registered via api.tool() survive reload.
    api_tools = set(_API_TOOL_REGISTRY.get(pid) or ())
    tool_names.update(api_tools)
    # plan_tools ⊆ declared tools when the manifest lists tools; otherwise keep as-is
    # (api.tool packs often omit tools[] and register at runtime).
    if declared_tools:
        plan_names = {n for n in plan_names if n in declared_tools or n in api_tools}
        destructive_names = {
            n for n in destructive_names if n in declared_tools or n in api_tools
        }

    if tool_names or tools_meta or plan_names or destructive_names:
        _CONTRIBUTIONS["agent_tools"][pid] = {
            "category": category,
            "tools": sorted(tool_names),
            "plan_tools": sorted(plan_names),
            "destructive_tools": sorted(destructive_names),
            "sensitive": sensitive,
        }
    if plan_names:
        _PLUGIN_PLAN_TOOLS.update(plan_names)
    if destructive_names:
        _PLUGIN_DESTRUCTIVE_TOOLS.update(destructive_names)
    if tool_names:
        _PLUGIN_TOOL_NAMES.update(tool_names)
        for name in tool_names:
            _PLUGIN_TOOL_OWNER[name] = pid
            _TOOL_LAST_OWNER[name] = pid
        _rebuild_intent_row(pid, tool_names, intent_re)

    if not register or pid in _REGISTERED:
        return
    # Its tools are listed from the cached schemas; the backend loads on the first tool
    # call or panel RPC (tool_registry.load_lazy_backend).
    if defer_plugin_register(pid, root, manifest):
        return
    # One import + register() per plugin at a time. The Store's enable worker and the
    # repair pass can reach here together; a compiled backend initialised twice at once
    # aborts the whole app (Python source only ran twice). The second loader waits, then
    # finds the plugin registered.
    lock = _backend_load_lock(pid)
    if not lock.acquire(timeout=_BACKEND_LOAD_WAIT_S):
        _log.warning("Plugin %s: still loading elsewhere after %ss; skipped", pid, _BACKEND_LOAD_WAIT_S)
        return
    try:
        if pid in _REGISTERED:
            return
        signature = _manifest_signature(root)  # before the import: a change during it reloads again
        version = str(manifest.get("version") or "")
        from backend.uefn_plugins.store import app_too_old_for

        too_old = app_too_old_for(manifest)
        if too_old:
            # Copied in by hand or installed before this check: never load what this Ducky can't run.
            _record_plugin_load_error(pid, RuntimeError(too_old))
            return
        if plugin_crashed_ducky(pid, version):
            label = str(manifest.get("label") or pid)
            _record_plugin_load_error(
                pid,
                RuntimeError(
                    f"UEFN Ducky closed while loading {label} {version}, so it was skipped to let "
                    "Ducky open. Ducky installs a newer version by itself as soon as the Store has one; "
                    "or turn it off and on in Settings → Plugins to try again."
                ),
            )
            return
        backend = manifest.get("backend") if isinstance(manifest.get("backend"), dict) else {}
        entry = str(backend.get("entry") or "backend").strip() or "backend"
        register_name = str(backend.get("register") or "register").strip() or "register"
        # Folders it keeps itself point at the copy it reads now before it imports or
        # registers anything (a broken junction is repaired).
        folder_epoch = _bind_folders(pid)
        marker = _load_marker_begin(pid, version)
        try:
            if _manifest_is_compiled(manifest):
                mod = _import_compiled_backend(pid, root, manifest)
            else:
                mod = _import_backend(pid, root, entry)
            if mod is None:
                return
            register_fn = getattr(mod, register_name, None)
            api = None
            if callable(register_fn):
                _clear_node_handlers(pid)  # register() names them all again
                api = recording_plugin_api(pid)
                try:
                    register_fn(api)
                except BaseException:
                    _clear_node_handlers(pid)
                    raise
        finally:
            # A Python error still reaches here; only a crash of the whole process
            # leaves the marker for the next launch to find.
            _load_marker_end(marker)
        _REGISTERED.add(pid)
        if api is not None:
            write_tool_schemas(pid, root, manifest, set(api._api_calls), set(api._node_types))
        _LOADED_SIGNATURE[pid] = signature
        _LOAD_FAILURES.pop(pid, None)
        pending = _RESTART_PENDING.pop(pid, None)
        if pending:
            _record_plugin_load_error(pid, pending)
        if folder_epoch is not None:
            _FOLDER_EPOCH[pid] = folder_epoch
            _FOLDER_MOVED.pop(pid, None)
    finally:
        lock.release()


_BACKEND_LOAD_LOCKS: dict[str, threading.RLock] = {}
_BACKEND_LOAD_LOCKS_GUARD = threading.Lock()
_BACKEND_LOAD_WAIT_S = 120.0


def _backend_load_lock(pid: str) -> threading.RLock:
    with _BACKEND_LOAD_LOCKS_GUARD:
        return _BACKEND_LOAD_LOCKS.setdefault(pid, threading.RLock())


# Names from the rest of the host, bound after every definition above so the host
# modules can import each other in any order.
from backend.uefn_plugins.plugin_host.api import _PluginApi  # noqa: E402
from backend.uefn_plugins.plugin_host.backends import (  # noqa: E402
    _import_backend,
    _import_compiled_backend,
    _manifest_is_compiled,
)
from backend.uefn_plugins.plugin_host.boot import _notify_uefn_plugins_changed  # noqa: E402
from backend.uefn_plugins.plugin_host.contributions import (  # noqa: E402
    _automation_contrib_row,
    _automation_template_row,
    _clear_node_handlers,
    _image_generator_row,
    _media_backend_row,
    _resolve_contrib_icon,
    _strip_contributions_for,
)
from backend.uefn_plugins.plugin_host.crash_guard import (  # noqa: E402
    _load_marker_begin,
    _load_marker_end,
    plugin_crashed_ducky,
)
from backend.uefn_plugins.plugin_host.errors import _record_plugin_load_error  # noqa: E402
from backend.uefn_plugins.plugin_host.folders import _bind_folders  # noqa: E402
from backend.uefn_plugins.plugin_host.lifecycle import (  # noqa: E402
    _manifest_signature,
    invalidate_plugin_runtime,
)
from backend.uefn_plugins.plugin_host.state import (  # noqa: E402
    _API_TOOL_REGISTRY,
    _CONTRIBUTIONS,
    _FOLDER_EPOCH,
    _FOLDER_MOVED,
    _LOAD_FAILURES,
    _LOAD_THREAD_STATE,
    _LOADED_SIGNATURE,
    _LOCK,
    _PLUGIN_DESTRUCTIVE_TOOLS,
    _PLUGIN_PLAN_TOOLS,
    _PLUGIN_TOOL_NAMES,
    _PLUGIN_TOOL_OWNER,
    _REGISTERED,
    _RESTART_PENDING,
    _TOOL_CONCURRENCY,
    _TOOL_LAST_OWNER,
    _TOOL_SLOTS,
    _log,
)
from backend.uefn_plugins.plugin_host.tool_registry import (  # noqa: E402
    _rebuild_intent_row,
    defer_plugin_register,
    lazy_eligible,
    manifest_tool_concurrency,
    recording_plugin_api,
    write_tool_schemas,
)
from backend.uefn_plugins.plugin_host.ui_registry import normalize_chat_reference  # noqa: E402
