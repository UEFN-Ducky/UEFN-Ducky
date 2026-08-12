"""Verse @editable property editor for UEFN Ducky.

Verse device references are stored on the placed ``VerseDevice_C`` actor as
``ScriptPropertyOverrides`` (protected from Python on the actor wrapper). The
working path is the inner ``Script`` subobject with compiler-mangled names:

    ``__verse_0x{HASH}_{FieldName}``

Hash discovery: scan ``ValkyrieUploadTemp`` / ``__ExternalActors__`` ``.uasset``
binaries for ``__verse_0x...`` strings (stable per compile).

Verse-to-Verse refs (``?player_manager``): pass the target device's ``Script`` object.
Creative device refs (``player_spawner_device``): create a wrapper under the manager
``Script``, assign it to ``AllPlayerSpawners``, then set ``SavedActor`` on each
wrapper to the ``BP_Creative_Player_Spawner_Prop_C`` spawn pad actor.
"""

from __future__ import annotations

import os
import re
from typing import Any, Dict, List, Optional, Tuple

import unreal

from listener import lookup
from listener.save_coalesce import request_level_save
from listener.serialize import is_live

_VERSE_PROP_RE = re.compile(rb"__verse_0x[0-9A-Fa-f]{8}_[A-Za-z0-9_]+")
_EDITABLE_RE = re.compile(
    r"^\s*@editable(?:\s+<[^>]+>)?\s*\n\s*([A-Za-z_][A-Za-z0-9_]*)(?:\s*<[^>]+>)*\s*:",
    re.MULTILINE,
)
_EDITABLE_INLINE_RE = re.compile(
    r"@editable\s+(?:<[^>]+>\s+)?([A-Za-z_][A-Za-z0-9_]*)(?:\s*<[^>]+>)?"
)

# VerseClass asset paths discovered at runtime via asset registry (no project hardcoding).

# Verse @editable type -> (wrapper VerseClass path, link property on wrapper).
# Used by wire_verse_* tools so agents never need project-specific setup scripts.
_WRAPPER_REGISTRY: Dict[str, Tuple[str, str]] = {
    "player_spawner_device": ("/CRD_PlayerSpawn/_Verse.player_spawner_device", "SavedActor"),
    "button_device": ("/CreativeCoreDevices/_Verse.button_device", "SavedActor"),
    "guard_spawner_device": ("/CRD_HenchmanSpawner/_Verse.guard_spawner_device", "SavedActor"),
    "npc_spawner_device": ("/CRD_NPCSpawner/_Verse.npc_spawner_device", "SavedActor"),
    "barrier_device": ("/CRD_VolumetricRegion/_Verse.barrier_device", "SavedActor"),
    "character_device": ("/CRD_Mannequin/_Verse.character_device", "SavedActor"),
    "item_spawner_device": ("/CreativeCoreDevices/_Verse.item_spawner_device", "SavedActor"),
    "item_granter_device": ("/CreativeCoreDevices/_Verse.item_granter_device", "SavedActor"),
    "conditional_button_device": (
        "/CreativeCoreDevices/_Verse.conditional_button_device",
        "SavedActor",
    ),
    "creative_prop": (
        "/VerseDevices/_Verse/VNI/VerseDevices.Devices_creative_prop",
        "SavedActor",
    ),
    "creative_prop_asset": (
        "/VerseDevices/_Verse/VNI/VerseDevices.Devices_creative_prop_asset",
        "AssetForEditor",
    ),
}

# Inline `@editable Chunks <public>: []rgd_chunk_entry` and multiline `Chunks: …`.
_FIELD_TYPE_RE = re.compile(
    r"^\s*(?:@editable\s+(?:<[^>]+>\s+)?)?([A-Za-z_][A-Za-z0-9_]*)(?:\s*<[^>]+>)*\s*:\s*([^=\n]+)",
    re.MULTILINE,
)

_HASH_CACHE: Optional[Dict[str, str]] = None
# Per Script-class verse source (avoids os.walk on every wire).
_VERSE_SOURCE_CACHE: Dict[str, Tuple[str, str, str]] = {}
# Per (class, field) inferred @editable type.
_FIELD_TYPE_CACHE: Dict[Tuple[str, str], Optional[str]] = {}
# Actors that passed full wiring readiness this session (key = actor path name).
_WIRING_READY_ACTORS: set[str] = set()
# Per Script class: field name -> mangled __verse_0x... property on the device.
_SCRIPT_PROPS_CACHE: Dict[str, Dict[str, str]] = {}
# Cached Content/Verse search roots (expensive to rebuild).
_VERSE_SEARCH_DIRS_CACHE: Optional[List[str]] = None
# field name -> (snippet text, file path) for class-agnostic @editable lookup.
_FIELD_SNIPPET_CACHE: Dict[str, Tuple[str, str]] = {}
# struct_key -> resolved Verse class object path (avoids search_all_assets(True) per call).
_STRUCT_CLASS_PATH_CACHE: Dict[str, str] = {}

_SCRIPT_PROP_RE = re.compile(r"__verse_0x[0-9A-Fa-f]{8}_(.+)")
# Cap a single .uasset/.umap read while hash-scanning — the mangled property
# name is near the export table, not deep in binary payload data.
_MAX_HASH_SCAN_FILE_BYTES = 4 * 1024 * 1024


def _disk_project_content_dir() -> str | None:
    """On-disk ``.../<Project>/Content`` — ``unreal.Paths.project_content_dir()`` is often virtual."""
    try:
        lib = unreal.ValkyrieProjectLibrary
        handle = lib.get_main_project()
        if handle and lib.is_project_handle_valid(handle):
            pd = str(lib.get_project_directory(handle))
            content = os.path.normpath(os.path.join(pd, "Content"))
            if os.path.isdir(content):
                return content
    except Exception:
        pass
    try:
        content = os.path.normpath(str(unreal.Paths.project_content_dir()))
        if os.path.isdir(content):
            return content
    except Exception:
        pass
    return None


def _valkyrie_upload_temp_roots() -> List[str]:
    roots: List[str] = []
    seen: set[str] = set()
    for env in ("LOCALAPPDATA", "APPDATA"):
        base = os.environ.get(env, "")
        if not base:
            continue
        valk = os.path.join(base, "UnrealEditorFortnite", "Intermediate", "ValkyrieUploadTemp")
        if os.path.isdir(valk) and valk not in seen:
            seen.add(valk)
            roots.append(valk)
    return roots


def _scan_roots() -> List[str]:
    roots: List[str] = []
    seen: set[str] = set()

    def _add(path: str) -> None:
        p = os.path.normpath(path)
        if p not in seen and os.path.isdir(p):
            seen.add(p)
            roots.append(p)

    disk_content = _disk_project_content_dir()
    if disk_content:
        _add(disk_content)
        _add(os.path.join(disk_content, "__ExternalActors__"))

    for valk in _valkyrie_upload_temp_roots():
        _add(valk)

    return roots


def _priority_hash_scan_dirs() -> List[str]:
    """Project-local paths scanned before the global file cap."""
    dirs: List[str] = []
    seen: set[str] = set()

    def _add(path: str) -> None:
        p = os.path.normpath(path)
        if p not in seen and os.path.isdir(p):
            seen.add(p)
            dirs.append(p)

    disk_content = _disk_project_content_dir()
    if disk_content:
        _add(os.path.join(disk_content, "__ExternalActors__"))
        _add(os.path.join(disk_content, "_Verse"))

    for valk in _valkyrie_upload_temp_roots():
        try:
            for root, dirnames, _ in os.walk(valk):
                depth = root[len(valk) :].count(os.sep)
                if depth > 10:
                    dirnames.clear()
                    continue
                if os.path.basename(root) == "Verse":
                    _add(root)
                for dn in list(dirnames):
                    if dn == "Verse":
                        _add(os.path.join(root, dn))
        except OSError:
            continue

    return dirs


def _augment_hash_cache(field: str, prop: str) -> None:
    global _HASH_CACHE
    if _HASH_CACHE is None:
        _HASH_CACHE = {}
    _HASH_CACHE[field] = prop


def _lookup_field_hash_in_dirs(
    field: str,
    dirs: List[str],
    *,
    max_files: int = 500,
) -> Optional[str]:
    """Targeted binary search for one field's mangled property name."""
    suffix = f"_{field}".encode()
    needle = re.compile(rb"__verse_0x[0-9A-Fa-f]{8}" + re.escape(suffix))
    seen = 0
    for d in dirs:
        if not d or not os.path.isdir(d):
            continue
        for dirpath, _dirnames, filenames in os.walk(d):
            depth = dirpath[len(d) :].count(os.sep)
            if depth > 14:
                continue
            for fn in filenames:
                if not fn.endswith((".uasset", ".umap")):
                    continue
                seen += 1
                if seen > max_files:
                    return None
                fp = os.path.join(dirpath, fn)
                try:
                    data = open(fp, "rb").read(_MAX_HASH_SCAN_FILE_BYTES)
                except OSError:
                    continue
                m = needle.search(data)
                if m:
                    return m.group().decode()
    return None


def _probe_script_for_field(script: Any, field: str, hashes: Dict[str, str]) -> Optional[str]:
    suffix = f"_{field}"
    for prop in hashes.values():
        if prop.endswith(suffix):
            try:
                script.get_editor_property(prop)
                _augment_hash_cache(field, prop)
                return prop
            except Exception:
                continue
    return None


def _cached_hashes() -> Dict[str, str]:
    """Return the hash map without triggering a full-disk scan."""
    if _HASH_CACHE is not None:
        return dict(_HASH_CACHE)
    return {}


def _script_hash_scan_dirs(script: Any) -> List[str]:
    """Disk folders likely holding this Script class's compiled Verse assets."""
    dirs: List[str] = []
    disk = _disk_project_content_dir()
    if not disk:
        return dirs
    try:
        cls_path = script.get_class().get_path_name()
        pkg = cls_path.rsplit(".", 1)[0]
        parts = pkg.strip("/").split("/", 1)
        if len(parts) == 2:
            rel = parts[1].replace("/", os.sep)
            folder = os.path.normpath(os.path.join(disk, rel))
            if os.path.isdir(folder):
                dirs.append(folder)
            parent = os.path.dirname(folder)
            if parent and os.path.isdir(parent):
                dirs.append(parent)
            grand = os.path.dirname(parent)
            if grand and os.path.isdir(grand) and os.path.basename(grand) == "_Verse":
                dirs.append(grand)
    except Exception:
        pass
    return dirs


def _wire_hash_search_dirs(script: Any) -> List[str]:
    """Small, fast search roots for one field during wiring (no global scan)."""
    seen: set[str] = set()
    out: List[str] = []
    for d in _script_hash_scan_dirs(script) + _priority_hash_scan_dirs():
        p = os.path.normpath(d)
        if p not in seen and os.path.isdir(p):
            seen.add(p)
            out.append(p)
    return out


def _resolve_field_prop_cheap(script: Any, field: str, hashes: Dict[str, str]) -> Optional[str]:
    """script_props / cached hashes / live property probe only — never touches disk."""
    prop = _script_verse_properties(script).get(field)
    if prop:
        return prop
    prop = hashes.get(field)
    if prop:
        return prop
    return _probe_script_for_field(script, field, hashes)


def _lookup_many_field_hashes_in_dirs(
    fields: List[str],
    dirs: List[str],
    *,
    max_files: int = 200,
) -> Dict[str, str]:
    """Resolve several missing fields in one directory walk instead of one walk each."""
    remaining = {f: re.compile(rb"__verse_0x[0-9A-Fa-f]{8}_" + re.escape(f.encode())) for f in fields}
    found: Dict[str, str] = {}
    if not remaining:
        return found
    seen = 0
    for d in dirs:
        if not d or not os.path.isdir(d):
            continue
        for dirpath, _dirnames, filenames in os.walk(d):
            for fn in filenames:
                if not fn.endswith((".uasset", ".umap")):
                    continue
                seen += 1
                if seen > max_files:
                    return found
                fp = os.path.join(dirpath, fn)
                try:
                    data = open(fp, "rb").read(_MAX_HASH_SCAN_FILE_BYTES)
                except OSError:
                    continue
                for field, needle in list(remaining.items()):
                    m = needle.search(data)
                    if m:
                        found[field] = m.group().decode()
                        del remaining[field]
                if not remaining:
                    return found
    return found


def _resolve_field_prop(script: Any, field: str, hashes: Dict[str, str]) -> Optional[str]:
    prop = _resolve_field_prop_cheap(script, field, hashes)
    if prop:
        return prop
    search_dirs = _wire_hash_search_dirs(script)
    prop = _lookup_field_hash_in_dirs(field, search_dirs, max_files=120)
    if prop:
        _augment_hash_cache(field, prop)
        cls_name = script.get_class().get_name()
        _SCRIPT_PROPS_CACHE.setdefault(cls_name, {})[field] = prop
        return prop
    return None


def _resolve_field_prop_for_wire(actor: unreal.Actor, script: Any, field: str) -> str:
    """Resolve one field for wiring — never runs the 2000-file global hash scan."""
    prop = _resolve_field_prop(script, field, _cached_hashes())
    if prop:
        try:
            script.get_editor_property(prop)
            return prop
        except Exception:
            pass

    _cls, verse_text, _fp = _verse_source_for_actor(actor)
    verse_fields = _parse_editables_from_verse(verse_text) if verse_text else []
    if field not in verse_fields:
        raise ValueError(_field_not_found_error(actor, script, field, verse_fields))

    prop = _lookup_field_hash_in_dirs(field, _wire_hash_search_dirs(script), max_files=120)
    if prop:
        try:
            script.get_editor_property(prop)
            _augment_hash_cache(field, prop)
            cls_name = script.get_class().get_name()
            _SCRIPT_PROPS_CACHE.setdefault(cls_name, {})[field] = prop
            return prop
        except Exception as exc:
            raise ValueError(
                f"Field {field!r} hash {prop!r} found in assets but not readable on Script: {exc}. "
                "Build Verse in UEFN, then reload_listener."
            ) from exc

    raise ValueError(
        f"Field {field!r} is in Verse source but has no compiled hash on this device yet. "
        "Build Verse Code in UEFN, then reload_listener and retry."
    )


def _require_field_for_wire(actor_path: str, field: str) -> tuple:
    """Lightweight preflight for wire_* — one field, no global hash scan."""
    actor = lookup.require_actor(actor_path)
    script = _verse_script(actor)
    prop = _resolve_field_prop_for_wire(actor, script, field)
    return actor, script, prop


def _scan_tree_for_hashes(
    root: str,
    found: Dict[str, str],
    visited_dirs: set[str],
    seen_files: int,
    max_files: int,
) -> int:
    """Walk one root looking for ``__verse_0x...`` strings, capped by *max_files*.

    *visited_dirs* is shared across every root in one ``_scan_property_hashes``
    call so overlapping roots (e.g. a priority dir that is also a subdirectory
    of a later global root) are never read twice.
    """
    for dirpath, _dirnames, filenames in os.walk(root):
        if dirpath in visited_dirs:
            continue
        visited_dirs.add(dirpath)
        if dirpath[len(root) :].count(os.sep) > 12:
            continue
        for fn in filenames:
            if not fn.endswith((".uasset", ".umap")):
                continue
            seen_files += 1
            if seen_files > max_files:
                return seen_files
            fp = os.path.join(dirpath, fn)
            try:
                data = open(fp, "rb").read(_MAX_HASH_SCAN_FILE_BYTES)
            except OSError:
                continue
            for m in _VERSE_PROP_RE.finditer(data):
                prop = m.group().decode()
                field = prop.rsplit("_", 1)[-1]
                found.setdefault(field, prop)
    return seen_files


def _scan_property_hashes(max_files: int = 800) -> Dict[str, str]:
    """Map Verse field name -> mangled ``__verse_0x...`` property name."""
    global _HASH_CACHE
    if _HASH_CACHE is not None:
        return dict(_HASH_CACHE)

    found: Dict[str, str] = {}
    visited_dirs: set[str] = set()
    seen_files = 0

    for priority in _priority_hash_scan_dirs():
        seen_files = _scan_tree_for_hashes(priority, found, visited_dirs, seen_files, max_files)
        if seen_files > max_files:
            break

    if seen_files <= max_files:
        for root in _scan_roots():
            seen_files = _scan_tree_for_hashes(root, found, visited_dirs, seen_files, max_files)
            if seen_files > max_files:
                break

    _HASH_CACHE = found
    return dict(found)


def _verse_script(actor: unreal.Actor) -> Any:
    if actor.get_class().get_name() != "VerseDevice_C":
        raise ValueError(f"Not a Verse device actor: {actor.get_actor_label()}")
    return actor.get_editor_property("Script")


def _mangled_name(field: str, script: Any = None, actor: unreal.Actor | None = None) -> str:
    if script is not None and actor is not None:
        return _resolve_field_prop_for_wire(actor, script, field)
    hashes = _cached_hashes()
    if script is not None:
        prop = _resolve_field_prop(script, field, hashes)
        if prop:
            return prop
    prop = hashes.get(field)
    if not prop and script is not None:
        prop = _lookup_field_hash_in_dirs(field, _wire_hash_search_dirs(script), max_files=120)
        if prop:
            _augment_hash_cache(field, prop)
    if not prop:
        raise ValueError(
            f"Unknown Verse field {field!r} — not on this device's Script. "
            "Call get_verse_editables(actor_path) for exact field names."
        )
    return prop


def _parse_editables_from_verse(content: str) -> List[str]:
    fields = _EDITABLE_RE.findall(content)
    fields.extend(_EDITABLE_INLINE_RE.findall(content))
    out: List[str] = []
    for f in fields:
        if f not in out:
            out.append(f)
    return out


def _script_class_stem(cls_name: str) -> str:
    """``Verse-random_room_orchestrator`` → ``random_room_orchestrator``."""
    stem = cls_name[6:] if cls_name.startswith("Verse-") else cls_name
    return stem.replace("-", "_")


def _script_class_stems(cls_name: str) -> List[str]:
    """Candidate Verse class name stems for .verse source lookup."""
    stem = _script_class_stem(cls_name)
    stems: List[str] = []
    # UEFN Script classes look like Verse-Folder-Subfolder-class_name_device — the
    # Verse source class is almost always the final dash segment (underscores intact).
    if cls_name.startswith("Verse-"):
        parts = cls_name[6:].split("-")
        if parts:
            last = parts[-1].strip()
            if last:
                stems.append(last)
    for s in (stem, stem.removesuffix("_device"), f"{stem}_device"):
        if s and s not in stems:
            stems.append(s)
    return stems


def _script_verse_properties(script: Any) -> Dict[str, str]:
    """Map @editable field name -> mangled property on this device's Script."""
    cls_name = script.get_class().get_name()
    cached = _SCRIPT_PROPS_CACHE.get(cls_name)
    if cached is not None:
        return cached

    found: Dict[str, str] = {}
    for name in dir(script):
        if not name.startswith("__verse_0x"):
            continue
        m = _SCRIPT_PROP_RE.match(name)
        if not m:
            continue
        field = m.group(1)
        try:
            script.get_editor_property(name)
            found.setdefault(field, name)
        except Exception:
            continue

    if not found:
        try:
            for prop in script.get_class().properties():
                pname = str(prop.get_fname())
                if not pname.startswith("__verse_0x"):
                    continue
                m = _SCRIPT_PROP_RE.match(pname)
                if not m:
                    continue
                field = m.group(1)
                try:
                    script.get_editor_property(pname)
                    found.setdefault(field, pname)
                except Exception:
                    continue
        except Exception:
            pass

    _SCRIPT_PROPS_CACHE[cls_name] = found
    return found


def _verse_search_dirs() -> List[str]:
    """All ``Content/Verse`` folders visible to the editor (handles path quirks)."""
    global _VERSE_SEARCH_DIRS_CACHE
    if _VERSE_SEARCH_DIRS_CACHE is not None:
        return _VERSE_SEARCH_DIRS_CACHE

    dirs: List[str] = []
    seen: set[str] = set()

    def _add(path: str) -> None:
        p = os.path.normpath(path)
        if p not in seen and os.path.isdir(p):
            seen.add(p)
            dirs.append(p)

    disk_content = _disk_project_content_dir()
    if disk_content:
        _add(os.path.join(disk_content, "Verse"))

    env_root = os.environ.get("UEFN_DUCKY_PROJECT_ROOT", "").strip()
    if env_root:
        for sub in ("Content/Verse", "Verse"):
            _add(os.path.join(env_root, sub.replace("/", os.sep)))

    try:
        import json as _json

        appdata = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA") or ""
        cfg_path = os.path.join(appdata, "UEFN-Ducky", "config.json")
        if appdata and os.path.isfile(cfg_path):
            cfg = _json.loads(open(cfg_path, encoding="utf-8").read())
            pr = str(cfg.get("project_root") or "").strip()
            if pr:
                for sub in ("Content/Verse", "Verse"):
                    _add(os.path.join(pr, sub.replace("/", os.sep)))
    except Exception:
        pass

    content = str(unreal.Paths.project_content_dir())
    project = str(unreal.Paths.project_dir())
    for base in (content, os.path.join(project, "Content"), project):
        _add(os.path.join(base, "Verse"))

    try:
        lib = unreal.ValkyrieProjectLibrary
        handle = lib.get_main_project()
        if handle and lib.is_project_handle_valid(handle):
            vp = str(lib.get_project_verse_path(handle))
            if vp:
                _add(vp)
                parent = os.path.dirname(vp)
                if parent:
                    _add(parent)
    except Exception:
        pass

    for valk in _valkyrie_upload_temp_roots():
        try:
            for root, dirnames, _ in os.walk(valk):
                depth = root[len(valk) :].count(os.sep)
                if depth > 10:
                    dirnames.clear()
                    continue
                if os.path.basename(root).lower() == "verse":
                    _add(root)
                for dn in list(dirnames):
                    if dn.lower() == "verse":
                        _add(os.path.join(root, dn))
        except OSError:
            pass

    # Any ``Verse`` folder under the project (small UEFN projects).
    try:
        for root, dirnames, _ in os.walk(project):
            depth = root[len(project) :].count(os.sep)
            if depth > 4:
                dirnames.clear()
                continue
            if os.path.basename(root).lower() == "verse":
                _add(root)
    except OSError:
        pass

    _VERSE_SEARCH_DIRS_CACHE = dirs
    return dirs


def _read_verse_file(fp: str) -> str:
    try:
        return open(fp, encoding="utf-8", errors="replace").read()
    except OSError:
        return ""


def _find_verse_source_by_stems(stems: List[str]) -> Tuple[str, str]:
    """Search Verse trees for ``stem := class`` definitions."""
    for stem in stems:
        # Allow Verse access/specifiers between name and := e.g. `foo <public> := class`
        class_re = re.compile(rf"(?i)\b{re.escape(stem)}(?:\s*<[^>]+>)*\s*:=\s*class\b")
        for verse_root in _verse_search_dirs():
            for dirpath, _dn, filenames in os.walk(verse_root):
                for fn in filenames:
                    if not fn.endswith(".verse"):
                        continue
                    fp = os.path.join(dirpath, fn)
                    text = _read_verse_file(fp)
                    if text and class_re.search(text):
                        return text, fp
    return "", ""


def _find_verse_snippet_for_field(field: str) -> Tuple[str, str]:
    """Find any .verse file declaring ``@editable`` *field* (class-agnostic fallback)."""
    cached = _FIELD_SNIPPET_CACHE.get(field)
    if cached is not None:
        return cached

    field_re = re.compile(
        rf"@editable\s+(?:<[^>]+>\s+)?{re.escape(field)}\s*:",
        re.IGNORECASE,
    )
    for verse_root in _verse_search_dirs():
        for dirpath, _dn, filenames in os.walk(verse_root):
            for fn in filenames:
                if not fn.endswith(".verse"):
                    continue
                fp = os.path.join(dirpath, fn)
                text = _read_verse_file(fp)
                if text and field_re.search(text):
                    result = (text, fp)
                    _FIELD_SNIPPET_CACHE[field] = result
                    return result
    result = ("", "")
    _FIELD_SNIPPET_CACHE[field] = result
    return result


def _fields_on_device_script(actor: unreal.Actor, hashes: Dict[str, str]) -> List[str]:
    """@editable fields present on this device's Script (compiled hashes)."""
    script = _verse_script(actor)
    props = _script_verse_properties(script)
    if props:
        return sorted(props.keys())
    found: List[str] = []
    for field, prop in hashes.items():
        try:
            script.get_editor_property(prop)
            found.append(field)
        except Exception:
            continue
    return sorted(found)


def _verse_source_for_actor(
    actor: unreal.Actor,
) -> Tuple[str, str, str]:
    """Return (script_class, verse_file_text, verse_file_path) for the actor's Script class."""
    script = _verse_script(actor)
    cls_name = script.get_class().get_name()
    cached = _VERSE_SOURCE_CACHE.get(cls_name)
    if cached is not None:
        return cached

    stems = _script_class_stems(cls_name)

    text, fp = _find_verse_source_by_stems(stems)
    if text:
        result = (cls_name, text, fp)
        _VERSE_SOURCE_CACHE[cls_name] = result
        return result

    content = str(unreal.Paths.project_content_dir())
    project = str(unreal.Paths.project_dir())
    for stem in stems:
        # Allow Verse access/specifiers between name and := e.g. `foo <public> := class`
        class_re = re.compile(rf"(?i)\b{re.escape(stem)}(?:\s*<[^>]+>)*\s*:=\s*class\b")
        for base in (content, project, os.path.join(project, "Content") if project else ""):
            if not base or not os.path.isdir(base):
                continue
            try:
                for dirpath, _dn, filenames in os.walk(base):
                    if dirpath.count(os.sep) - base.count(os.sep) > 10:
                        continue
                    for fn in filenames:
                        if not fn.endswith(".verse"):
                            continue
                        fp = os.path.join(dirpath, fn)
                        text = _read_verse_file(fp)
                        if text and class_re.search(text):
                            result = (cls_name, text, fp)
                            _VERSE_SOURCE_CACHE[cls_name] = result
                            return result
            except OSError:
                continue

    result = (cls_name, "", "")
    _VERSE_SOURCE_CACHE[cls_name] = result
    return result


def _normalize_verse_type(raw: str) -> str:
    """``[]creative_prop`` -> ``creative_prop``; ``?player_manager`` -> ``player_manager``."""
    t = raw.strip()
    if t.startswith("[]"):
        t = t[2:].strip()
    if t.startswith("?"):
        t = t[1:].strip()
    return t.split("=")[0].strip()


def _infer_field_type(actor: unreal.Actor, field: str) -> Optional[str]:
    script = _verse_script(actor)
    cls_name = script.get_class().get_name()
    cache_key = (cls_name, field)
    if cache_key in _FIELD_TYPE_CACHE:
        return _FIELD_TYPE_CACHE[cache_key]

    _cls, text, _fp = _verse_source_for_actor(actor)
    if text:
        for match in _FIELD_TYPE_RE.finditer(text):
            if match.group(1) == field:
                result = _normalize_verse_type(match.group(2))
                _FIELD_TYPE_CACHE[cache_key] = result
                return result
    snippet, _ = _find_verse_snippet_for_field(field)
    if snippet:
        for match in _FIELD_TYPE_RE.finditer(snippet):
            if match.group(1) == field:
                result = _normalize_verse_type(match.group(2))
                _FIELD_TYPE_CACHE[cache_key] = result
                return result
    _FIELD_TYPE_CACHE[cache_key] = None
    return None


def _field_types_from_verse(verse_text: str) -> Dict[str, str]:
    """Parse ``field -> normalized type`` from one .verse source."""
    types: Dict[str, str] = {}
    if not verse_text:
        return types
    for match in _FIELD_TYPE_RE.finditer(verse_text):
        types[match.group(1)] = _normalize_verse_type(match.group(2))
    return types


def _field_is_array_in_verse(verse_text: str, field: str) -> bool:
    if not verse_text:
        return False
    for match in _FIELD_TYPE_RE.finditer(verse_text):
        if match.group(1) == field:
            return match.group(2).strip().startswith("[]")
    return False


def _suggest_similar_fields(script: Any, field: str, verse_fields: List[str]) -> List[str]:
    """Names on this device that look like a mistyped *field*."""
    needle = field.lower().replace("_", "")
    candidates = set(_script_verse_properties(script).keys())
    candidates.update(verse_fields)
    out: List[str] = []
    for name in sorted(candidates):
        norm = name.lower().replace("_", "")
        if needle in norm or norm in needle:
            out.append(name)
    return out[:12]


def _wrapper_spec_from_script_value(script: Any, prop: str) -> Optional[Tuple[str, str]]:
    try:
        val = script.get_editor_property(prop)
    except Exception:
        return None
    if val is None:
        return None
    if hasattr(val, "__len__") and not isinstance(val, (str, bytes)):
        if len(val) == 0:
            return None
        val = val[0]
    if not getattr(val, "get_class", None):
        return None
    cls = val.get_class()
    for link in ("SavedActor", "AssetForEditor"):
        try:
            val.get_editor_property(link)
            return cls.get_path_name(), link
        except Exception:
            continue
    return None


def _wiring_hint_from_meta(
    verse_type: Optional[str],
    is_array: bool,
    spec: Optional[Tuple[str, str]],
) -> dict:
    hint: dict = {"verse_type": verse_type, "is_array": is_array}
    if spec:
        hint["wrapper_class"] = spec[0]
        hint["link_property"] = spec[1]
    if verse_type and (
        verse_type.endswith("_device") or verse_type == "creative_prop"
    ):
        hint["tool"] = "wire_verse_device_array" if is_array else "wire_verse_device_ref"
    elif verse_type == "creative_prop_asset":
        hint["tool"] = "wire_verse_prop_assets"
    elif verse_type and not verse_type.endswith("_device") and verse_type not in (
        "creative_prop",
        "creative_prop_asset",
        "int",
        "float",
        "logic",
        "string",
    ):
        hint["tool"] = "set_verse_editable"
        hint["note"] = "Verse-to-Verse ref — pass target Verse device label"
    elif verse_type in ("int", "float", "logic", "string"):
        hint["tool"] = "set_verse_editable"
        hint["note"] = "Scalar — pass value="
    return hint


def _is_array_field(actor: unreal.Actor, field: str) -> bool:
    script = _verse_script(actor)
    props = _script_verse_properties(script)
    if field in props:
        try:
            val = script.get_editor_property(props[field])
            return val is not None and hasattr(val, "__len__") and not isinstance(
                val, (str, bytes)
            )
        except Exception:
            pass
    _cls, text, _fp = _verse_source_for_actor(actor)
    for src in (text, _find_verse_snippet_for_field(field)[0]):
        if not src:
            continue
        for match in _FIELD_TYPE_RE.finditer(src):
            if match.group(1) == field:
                return match.group(2).strip().startswith("[]")
    return False


def _wrapper_spec_for_type(verse_type: str) -> Optional[Tuple[str, str]]:
    base = _normalize_verse_type(verse_type)
    return _WRAPPER_REGISTRY.get(base)


def _wrapper_spec_for_field(actor: unreal.Actor, field: str) -> Optional[Tuple[str, str]]:
    script = _verse_script(actor)
    props = _script_verse_properties(script)
    prop = props.get(field)
    if prop:
        spec = _wrapper_spec_from_script_value(script, prop)
        if spec:
            return spec
    verse_type = _infer_field_type(actor, field)
    if verse_type:
        spec = _wrapper_spec_for_type(verse_type)
        if spec:
            return spec
    prop = prop or _resolve_field_prop(script, field, _cached_hashes())
    if not prop:
        return None
    try:
        existing = script.get_editor_property(prop)
    except Exception:
        existing = None
    if existing is not None and getattr(existing, "get_class", None):
        cls = existing.get_class()
        for link in ("SavedActor", "AssetForEditor"):
            try:
                existing.get_editor_property(link)
                return cls.get_path_name(), link
            except Exception:
                continue
    return None


def _wiring_hint(actor: unreal.Actor, field: str) -> dict:
    verse_type = _infer_field_type(actor, field)
    is_array = _is_array_field(actor, field)
    spec = _wrapper_spec_for_field(actor, field)
    hint: dict = {
        "verse_type": verse_type,
        "is_array": is_array,
    }
    if spec:
        hint["wrapper_class"] = spec[0]
        hint["link_property"] = spec[1]
    if verse_type and (
        verse_type.endswith("_device") or verse_type == "creative_prop"
    ):
        hint["tool"] = (
            "wire_verse_device_array" if is_array else "wire_verse_device_ref"
        )
    elif verse_type == "creative_prop_asset":
        hint["tool"] = "wire_verse_prop_assets"
    elif verse_type and not verse_type.endswith("_device") and verse_type not in (
        "creative_prop",
        "creative_prop_asset",
        "int",
        "float",
        "logic",
        "string",
    ):
        hint["tool"] = "set_verse_editable"
        hint["note"] = "Verse-to-Verse ref — pass target Verse device label"
    elif verse_type in ("int", "float", "logic", "string"):
        hint["tool"] = "set_verse_editable"
        hint["note"] = "Scalar — pass value="
    return hint


def list_verse_reference_types() -> dict:
    """Catalog of Verse reference types agents use when wiring @editables."""
    entries = []
    for verse_type, (cls_path, link_prop) in sorted(_WRAPPER_REGISTRY.items()):
        entries.append(
            {
                "verse_type": verse_type,
                "wrapper_class": cls_path,
                "link_property": link_prop,
                "scalar_tool": "wire_verse_device_ref",
                "array_tool": (
                    "wire_verse_prop_assets"
                    if verse_type == "creative_prop_asset"
                    else "wire_verse_device_array"
                ),
            }
        )
    return {
        "reference_types": entries,
        "patterns": {
            "verse_to_verse": "set_verse_editable(source, field, target_path=target_verse_device)",
            "creative_device_scalar": "wire_verse_device_ref(source, field, target_path=level_device)",
            "creative_device_array": "wire_verse_device_array(source, field, target_paths=[...])",
            "creative_prop_array": "wire_verse_device_array(source, field, target_paths=[prop_actors])",
            "prop_asset_array": "wire_verse_prop_assets(source, field, asset_paths=[/Game/...])",
            "texture_icon": "set_verse_texture_icon(source, icon_field, texture_path, ...)",
            "player_spawners": "wire_player_spawners(manager, spawn_pad_paths=[...])",
        },
        "verify": "get_verse_editables(source) — check SavedActor / AssetForEditor read-back",
    }


def wire_verse_device_ref(
    actor_path: str,
    field: str,
    target_path: str,
) -> dict:
    """Wire one scalar creative-device or creative_prop @editable via wrapper SavedActor."""
    actor, script, prop = _require_field_for_wire(actor_path, field)
    from listener.script_property_overrides import mark_verse_wiring_overrides

    target = lookup.require_actor(target_path)
    if target.get_class().get_name() == "VerseDevice_C":
        return set_verse_editable(actor_path, field, target_path=target_path)

    spec = _wrapper_spec_for_field(actor, field)
    if not spec:
        raise ValueError(
            f"Cannot infer wrapper type for {field!r}. "
            "Call list_verse_reference_types or get_verse_editables for hints."
        )
    cls_path, link_prop = spec
    cls = _load_verse_class(cls_path)

    with unreal.ScopedEditorTransaction(f"MCP Wire {field}"):
        wrapper = script.get_editor_property(prop)
        if wrapper is None or not getattr(wrapper, "get_class", None):
            wrapper = unreal.new_object(cls, script)
            script.set_editor_property(prop, wrapper)
        wrapper.set_editor_property(link_prop, target)
        wrapper.modify()
        script.modify()
        mark_verse_wiring_overrides(actor, script=script, scalar_prop=prop)
        actor.modify()
        linked = wrapper.get_editor_property(link_prop)

    return {
        "actor_path": actor.get_path_name(),
        "field": field,
        "mangled_name": prop,
        "target": target.get_actor_label(),
        "wrapper_class": cls_path,
        "link_property": link_prop,
        "linked": str(linked),
        "ok": linked is not None,
    }


def wire_verse_device_array(
    actor_path: str,
    field: str,
    target_paths: List[str],
) -> dict:
    """Wire one creative device or prop into an array @editable (single target per call)."""
    if len(target_paths) != 1:
        raise ValueError(
            "wire_verse_device_array accepts exactly one target per call. "
            "Call once per target, or use resize_verse_array_field + patch_verse_array_entry."
        )
    actor, script, prop = _require_field_for_wire(actor_path, field)
    from listener.script_property_overrides import mark_verse_wiring_overrides

    if not _is_array_field(actor, field):
        _cls, verse_text, _fp = _verse_source_for_actor(actor)
        verse_fields = _parse_editables_from_verse(verse_text) if verse_text else []
        similar = _suggest_similar_fields(script, field, verse_fields)
        raise ValueError(
            f"Field {field!r} is not an array @editable on {actor.get_actor_label()!r}. "
            "Use wire_verse_device_ref for each scalar field "
            f"(e.g. NPCSpawnerStaging, NPCSpawner1…). Similar: {similar or verse_fields[:8]}."
        )
    spec = _wrapper_spec_for_field(actor, field)
    if not spec:
        raise ValueError(f"Cannot infer wrapper type for array field {field!r}")
    cls_path, link_prop = spec
    cls = _load_verse_class(cls_path)

    targets = [lookup.require_actor(p) for p in target_paths]
    links = []

    with unreal.ScopedEditorTransaction(f"MCP Wire {field} array"):
        existing = list(script.get_editor_property(prop) or [])
        for target in targets:
            wrapper = unreal.new_object(cls, script)
            wrapper.set_editor_property(link_prop, target)
            wrapper.modify()
            existing.append(wrapper)
            links.append(
                {
                    "target": target.get_actor_label(),
                    "wrapper": str(wrapper.get_fname()),
                    "linked": str(wrapper.get_editor_property(link_prop)),
                }
            )
        script.set_editor_property(prop, existing)
        script.modify()
        mark_verse_wiring_overrides(actor, script=script, array_prop=prop)
        actor.modify()

    return {
        "actor_path": actor.get_path_name(),
        "field": field,
        "mangled_name": prop,
        "count": len(existing),
        "links": links,
        "ok": all(link["linked"] != "None" for link in links),
    }


def wire_verse_prop_assets(
    actor_path: str,
    field: str,
    asset_paths: List[str],
) -> dict:
    """Wire one creative_prop_asset path (single asset per call)."""
    if len(asset_paths) != 1:
        raise ValueError(
            "wire_verse_prop_assets accepts exactly one asset path per call. "
            "Call once per asset."
        )
    _require_field_for_wire(actor_path, field)
    from listener.script_property_overrides import mark_verse_wiring_overrides

    actor = lookup.require_actor(actor_path)
    script = _verse_script(actor)
    prop = _resolve_field_prop_for_wire(actor, script, field)
    cls_path, link_prop = _WRAPPER_REGISTRY["creative_prop_asset"]
    cls = _load_verse_class(cls_path)

    wrappers = []
    links = []

    with unreal.ScopedEditorTransaction(f"MCP Wire {field} prop assets"):
        for asset_path in asset_paths:
            obj = unreal.load_object(None, asset_path)
            if not obj:
                raise ValueError(f"Failed to load prop asset: {asset_path}")
            wrapper = unreal.new_object(cls, script)
            wrapper.set_editor_property(link_prop, obj)
            wrapper.modify()
            wrappers.append(wrapper)
            links.append(
                {
                    "asset_path": asset_path,
                    "wrapper": str(wrapper.get_fname()),
                    "linked": str(wrapper.get_editor_property(link_prop)),
                }
            )
        script.set_editor_property(prop, wrappers)
        script.modify()
        mark_verse_wiring_overrides(actor, script=script, array_prop=prop)
        actor.modify()

    return {
        "actor_path": actor.get_path_name(),
        "field": field,
        "mangled_name": prop,
        "count": len(wrappers),
        "links": links,
        "ok": all(link["linked"] != "None" for link in links),
    }


def _resolve_target(field: str, target_path: str, parent_script: Any = None) -> Any:
    """Resolve ``target_path`` for ``set_verse_editable`` (Verse-to-Verse refs only).

    Creative device / creative_prop refs must use ``wire_verse_device_ref`` or
    ``wire_verse_device_array`` — assigning level actors directly breaks wrappers.
    """
    target = lookup.require_actor(target_path)
    target_cls = target.get_class().get_name()

    if target_cls == "VerseDevice_C":
        return target.get_editor_property("Script")

    if parent_script is not None:
        if "Spawner" in field or field == "AllPlayerSpawners":
            if target_cls == "BP_Creative_Player_Spawner_Prop_C":
                ps_cls = _load_verse_class("/CRD_PlayerSpawn/_Verse.player_spawner_device")
                return unreal.new_object(ps_cls, parent_script)
        if "Icon" in field or "texture" in field.lower():
            icls = unreal.load_class(None, "/VerseEngine/_Verse/VNI/VerseAssets.Assets_texture")
            return unreal.new_object(icls, parent_script)

    return target


def list_verse_property_hashes(refresh: bool = False) -> dict:
    global _HASH_CACHE
    if refresh:
        _HASH_CACHE = None
        _WIRING_READY_ACTORS.clear()
        _VERSE_SOURCE_CACHE.clear()
        _FIELD_TYPE_CACHE.clear()
        _SCRIPT_PROPS_CACHE.clear()
        _FIELD_SNIPPET_CACHE.clear()
        _STRUCT_CLASS_PATH_CACHE.clear()
    hashes = _scan_property_hashes()
    return {"properties": hashes, "count": len(hashes)}


def _field_not_found_error(
    actor: unreal.Actor,
    script: Any,
    field: str,
    verse_fields: List[str],
) -> str:
    suggestions = _suggest_similar_fields(script, field, verse_fields)
    msg = (
        f"Field {field!r} not found on {actor.get_actor_label()!r}. "
        "Call get_verse_editables for exact @editable names from the Verse source."
    )
    if suggestions:
        msg += f" Similar fields on this device: {suggestions}."
    return msg


def _require_can_wire(actor_path: str, field: str = "") -> None:
    """Raise with STOP message if Verse is not compiled enough to wire."""
    actor = lookup.require_actor(actor_path)
    actor_key = actor.get_path_name()
    script = _verse_script(actor)
    hashes = _cached_hashes()
    script_props = _script_verse_properties(script)

    if actor_key in _WIRING_READY_ACTORS:
        if field and field not in script_props and _resolve_field_prop(script, field, hashes) is None:
            _cls, verse_text, _fp = _verse_source_for_actor(actor)
            verse_fields = _parse_editables_from_verse(verse_text) if verse_text else []
            raise ValueError(_field_not_found_error(actor, script, field, verse_fields))
        return

    _cls, verse_text, _fp = _verse_source_for_actor(actor)
    verse_fields = _parse_editables_from_verse(verse_text) if verse_text else []
    if not verse_fields:
        verse_fields = sorted(script_props.keys())
    resolved = dict(hashes)
    resolved.update(script_props)
    # Cheap-only pass (reflection + cache, no disk) for overall readiness — only
    # the field actually being wired is worth a targeted disk search below.
    for f in verse_fields:
        prop = _resolve_field_prop_cheap(script, f, resolved)
        if prop:
            resolved[f] = prop
    readiness = _wiring_readiness(verse_fields, resolved)
    if not readiness.get("can_wire"):
        raise ValueError(
            f"STOP — wiring blocked ({readiness.get('status')}): "
            f"{readiness.get('message')} {readiness.get('next_step')}"
        )
    if field and field not in resolved and _resolve_field_prop(script, field, resolved) is None:
        raise ValueError(_field_not_found_error(actor, script, field, verse_fields))
    _WIRING_READY_ACTORS.add(actor_key)


def _wiring_readiness(verse_fields: List[str], hashes: Dict[str, str]) -> dict:
    if not verse_fields:
        if hashes:
            return {
                "status": "ready_hashes_only",
                "can_wire": True,
                "message": (
                    "Verse .verse source not matched for Script class, but compiled "
                    "@editable fields were found on the device Script."
                ),
                "next_step": "Use wire_verse_device_ref / resize_verse_array / patch_verse_array_entry.",
            }
        return {
            "status": "no_verse_source",
            "can_wire": False,
            "message": (
                "Could not read @editable fields from Content/Verse/*.verse for this Script class. "
                "Check the Verse source file exists and the device Script class matches."
            ),
            "next_step": "Fix Verse source path or Script class; do not use execute_python to probe.",
        }
    hashed = [f for f in verse_fields if hashes.get(f)]
    if not hashed:
        return {
            "status": "verse_compile_required",
            "can_wire": False,
            "message": (
                f"Found {len(verse_fields)} @editable field(s) in .verse source but zero mangled "
                "property hashes — Verse is not compiled yet."
            ),
            "next_step": (
                "STOP. Tell the user: Build Verse Code in UEFN (Verse Explorer → Build). "
                "After build: list_verse_property_hashes(refresh=true), then wire with wire_verse_* tools."
            ),
            "fields_from_source": verse_fields,
        }
    if len(hashed) < len(verse_fields):
        missing = [f for f in verse_fields if f not in hashes]
        return {
            "status": "partial",
            "can_wire": True,
            "message": (
                f"{len(hashed)}/{len(verse_fields)} fields in global hash cache; "
                f"missing from cache: {missing[:12]}. "
                "This does NOT always mean Verse is uncompiled — tools resolve hashes on write."
            ),
            "next_step": (
                "Proceed with set_currency_config_entries / wire_verse_device_ref / patch_verse_array_entry. "
                "If a write fails, list_verse_property_hashes(refresh=true)."
            ),
        }
    return {
        "status": "ready",
        "can_wire": True,
        "message": "Verse compiled — use wire_verse_device_ref / wire_verse_device_array / wire_verse_prop_assets.",
        "next_step": "Wire each field per wiring.tool hint, verify with get_verse_editables, save_current_level.",
    }


def get_verse_editables(actor_path: str, *, include_wiring_hints: bool = True) -> dict:
    actor = lookup.require_actor(actor_path)
    script = _verse_script(actor)
    cls_name = script.get_class().get_name()
    hashes = _cached_hashes()
    script_props = _script_verse_properties(script)
    _cls, verse_text, verse_file = _verse_source_for_actor(actor)
    verse_fields = _parse_editables_from_verse(verse_text) if verse_text else []
    if not verse_fields:
        verse_fields = sorted(script_props.keys())
    verse_types = _field_types_from_verse(verse_text) if verse_text else {}
    source_mode = "verse_file" if verse_text else ("script_props" if script_props else "none")

    resolved_hashes = dict(hashes)
    resolved_hashes.update(script_props)

    # Cheap-only pass first (reflection + cache, no disk); then one combined
    # disk walk for whatever is still missing instead of one walk per field.
    prelim: Dict[str, Optional[str]] = {}
    missing_fields: List[str] = []
    for field in verse_fields:
        prop = _resolve_field_prop_cheap(script, field, resolved_hashes)
        prelim[field] = prop
        if prop:
            resolved_hashes[field] = prop
        else:
            missing_fields.append(field)
    if missing_fields:
        found = _lookup_many_field_hashes_in_dirs(
            missing_fields, _wire_hash_search_dirs(script), max_files=200
        )
        for f, prop in found.items():
            prelim[f] = prop
            resolved_hashes[f] = prop
            _augment_hash_cache(f, prop)
            _SCRIPT_PROPS_CACHE.setdefault(cls_name, {})[f] = prop

    settings: Dict[str, dict] = {}
    for field in verse_fields:
        entry: dict = {"field": field}
        prop = prelim.get(field)
        verse_type = verse_types.get(field)
        is_array = _field_is_array_in_verse(verse_text, field) if verse_text else False
        if prop:
            entry["mangled_name"] = prop
            if prop != hashes.get(field):
                entry["hash_source"] = "script" if field in script_props else "resolved"
            try:
                val = script.get_editor_property(prop)
                entry["readable"] = True
                entry["value"] = str(val)
                entry["overridden"] = bool(script.is_editor_property_overridden(prop))
                if not is_array:
                    is_array = val is not None and hasattr(val, "__len__") and not isinstance(
                        val, (str, bytes)
                    )
                try:
                    entry["array_length"] = len(val) if val is not None else 0
                except Exception:
                    pass
            except Exception as exc:
                entry["readable"] = False
                entry["error"] = str(exc)[:200]
        else:
            entry["mangled_name"] = None
            entry["note"] = (
                "Property hash not on Script yet — Build Verse if STOP is true; "
                "otherwise field exists in source only."
            )
        if include_wiring_hints:
            spec = _wrapper_spec_for_type(verse_type) if verse_type else None
            if spec is None and prop:
                spec = _wrapper_spec_from_script_value(script, prop)
            entry["wiring"] = _wiring_hint_from_meta(verse_type, is_array, spec)
        settings[field] = entry

    readiness = _wiring_readiness(verse_fields, resolved_hashes)

    return {
        "actor_path": actor.get_path_name(),
        "label": actor.get_actor_label(),
        "script_class": cls_name,
        "verse_source": verse_file or None,
        "verse_source_mode": source_mode,
        "editables": settings,
        "field_count": len(settings),
        "wiring": readiness,
        "STOP": not readiness.get("can_wire", False),
        "allowed_next_tools": (
            ["list_verse_property_hashes", "get_verse_editables", "ping", "reload_listener"]
            if not readiness.get("can_wire")
            else [
                "wire_verse_device_ref",
                "wire_verse_device_array",
                "wire_verse_prop_assets",
                "set_verse_editable",
                "wire_player_spawners",
                "set_verse_texture_icon",
                "resize_verse_array_field",
                "patch_verse_array_entry",
                "get_verse_editables",
                "save_current_level",
            ]
        ),
        "forbidden_until_compiled": (
            ["execute_python", "wire_verse_device_ref", "wire_verse_device_array", "wire_verse_prop_assets"]
            if not readiness.get("can_wire")
            else []
        ),
    }


def set_verse_editable(
    actor_path: str,
    field: str,
    target_path: str = "",
    value: Any = None,
) -> dict:
    """Set one @editable field on a Verse device.

    For device-reference fields pass ``target_path`` (label or path).
    For scalars pass ``value`` directly.
    """
    actor = lookup.require_actor(actor_path)

    if target_path:
        target = lookup.require_actor(target_path)
        if target.get_class().get_name() != "VerseDevice_C":
            spec = _wrapper_spec_for_field(actor, field)
            if spec and not _is_array_field(actor, field):
                return wire_verse_device_ref(actor_path, field, target_path)

    _require_field_for_wire(actor_path, field)
    script = _verse_script(actor)
    prop = _resolve_field_prop_for_wire(actor, script, field)

    with unreal.ScopedEditorTransaction(f"MCP Set Verse Editable {field}"):
        try:
            before = script.get_editor_property(prop)
        except Exception:
            before = None

        if target_path:
            coerced = _resolve_target(field, target_path, parent_script=script)
        else:
            coerced = value

        script.set_editor_property(prop, coerced)
        script.modify()
        actor.modify()

        try:
            after = script.get_editor_property(prop)
        except Exception:
            after = str(coerced)

    return {
        "actor_path": actor.get_path_name(),
        "field": field,
        "mangled_name": prop,
        "before": str(before),
        "after": str(after),
        "overridden": bool(script.is_editor_property_overridden(prop)),
        "ok": after is not None and str(after) not in ("None", "[]", "[None, None]"),
    }


def _load_verse_class(path: str) -> Any:
    cls = unreal.load_class(None, path)
    if not cls:
        raise ValueError(f"Verse class not found: {path}")
    return cls


def _find_verse_struct_class_path(struct_key: str) -> Optional[str]:
    reg = unreal.AssetRegistryHelpers.get_asset_registry()
    project = str(unreal.Paths.get_project_file_path()).rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
    project = project.rsplit(".", 1)[0]
    verse_path = f"/{project}/_Verse"
    needle = struct_key.replace("_", "-")
    matches: List[str] = []
    for ad in reg.get_assets_by_path(verse_path, True):
        name = str(ad.asset_name)
        if struct_key in name or needle in name:
            matches.append(f"{ad.package_name}.{name}")
    if not matches:
        return None
    matches.sort(key=lambda p: (0 if p.endswith(f".{struct_key}") else 1, p))
    return matches[0]


def _resolve_verse_struct_class(struct_key: str) -> Any:
    """Load a Verse struct class by key, cached per struct_key for the session."""
    cached_path = _STRUCT_CLASS_PATH_CACHE.get(struct_key)
    if cached_path:
        cls = unreal.load_class(None, cached_path)
        if cls:
            return cls
        _STRUCT_CLASS_PATH_CACHE.pop(struct_key, None)

    path = _find_verse_struct_class_path(struct_key)
    if not path:
        # Registry may not have indexed this project subtree yet — one-time fallback.
        unreal.AssetRegistryHelpers.get_asset_registry().search_all_assets(True)
        path = _find_verse_struct_class_path(struct_key)
    if not path:
        raise ValueError(f"Verse struct {struct_key!r} not found under _Verse. Recompile Verse.")
    cls = unreal.load_class(None, path)
    if not cls:
        raise ValueError(f"Failed to load Verse struct class: {path}")
    _STRUCT_CLASS_PATH_CACHE[struct_key] = path
    return cls


def _new_struct_instance(struct_key: str, outer: Any = None) -> Any:
    cls = _resolve_verse_struct_class(struct_key)
    return unreal.new_object(cls, outer) if outer else unreal.new_object(cls)


def set_level_thresholds(actor_path: str, threshold_xp_values: List[int]) -> dict:
    """Set a ``Levels`` array with ``ThresholdXP`` entries on a level-system Verse device."""
    actor = lookup.require_actor(actor_path)
    script = _verse_script(actor)
    levels_prop = _mangled_name("Levels")
    xp_prop = _mangled_name("ThresholdXP")
    entries = []
    with unreal.ScopedEditorTransaction("MCP Set Level Thresholds"):
        for xp in threshold_xp_values:
            entry = _new_struct_instance("player_level_threshold")
            entry.set_editor_property(xp_prop, int(xp))
            entries.append(entry)
        script.set_editor_property(levels_prop, entries)
        script.modify()
        actor.modify()
    return {
        "actor_path": actor.get_path_name(),
        "count": len(entries),
        "thresholds": list(threshold_xp_values),
        "ok": len(script.get_editor_property(levels_prop)) == len(entries),
    }


def resize_verse_array_field(actor_path: str, array_field: str, count: int) -> dict:
    """Set length of any ``[]struct`` @editable array (new rows are empty defaults)."""
    actor = lookup.require_actor(actor_path)
    if not _is_array_field(actor, array_field):
        raise ValueError(f"{array_field!r} is not an array @editable on this device")
    element_type = _infer_field_type(actor, array_field)
    if not element_type:
        raise ValueError(
            f"Cannot infer struct type for {array_field!r} — build Verse and inspect_verse_device first"
        )
    script = _verse_script(actor)
    prop = _mangled_name(array_field)
    n = max(0, int(count))
    with unreal.ScopedEditorTransaction("MCP Resize Verse Array"):
        entries = [_new_struct_instance(element_type) for _ in range(n)]
        script.set_editor_property(prop, entries)
        script.modify()
        actor.modify()
    actual = script.get_editor_property(prop)
    return {
        "actor_path": actor.get_path_name(),
        "array_field": array_field,
        "element_type": element_type,
        "count": len(actual) if actual is not None else 0,
        "ok": actual is not None and len(actual) == n,
    }


def patch_verse_array_entry(
    actor_path: str,
    array_field: str,
    index: int,
    properties: Dict[str, Any],
) -> dict:
    """Set subfields on one row of a ``[]struct`` @editable array."""
    actor = lookup.require_actor(actor_path)
    script = _verse_script(actor)
    prop = _mangled_name(array_field)
    rows = list(script.get_editor_property(prop) or [])
    idx = int(index)
    if idx < 0 or idx >= len(rows):
        raise ValueError(
            f"index {idx} out of range for {array_field!r} (length {len(rows)}). "
            "Call resize_verse_array first."
        )
    row = rows[idx]
    applied: Dict[str, Any] = {}
    texture_results: Dict[str, dict] = {}
    with unreal.ScopedEditorTransaction("MCP Patch Verse Array Entry"):
        for subfield, value in properties.items():
            if isinstance(value, dict) and value.get("texture_path"):
                icon_field = str(value.get("icon_field", subfield))
                tr = set_verse_texture_icon(
                    actor_path,
                    icon_field,
                    str(value["texture_path"]),
                    array_field=array_field,
                    entry_index=idx,
                )
                texture_results[subfield] = tr
                applied[subfield] = value
            else:
                sub_prop = _mangled_name(subfield)
                row.set_editor_property(sub_prop, value)
                applied[subfield] = value
        rows[idx] = row
        script.set_editor_property(prop, rows)
        script.modify()
        actor.modify()
    return {
        "actor_path": actor.get_path_name(),
        "array_field": array_field,
        "index": idx,
        "applied": applied,
        "textures": texture_results or None,
        "ok": True,
    }


def set_currency_config_entries(
    actor_path: str,
    count: int = 0,
    entries: Optional[List[Dict[str, Any]]] = None,
) -> dict:
    """Create ``CurrencyConfigs`` rows on a player wallet Verse device.

    Pass ``entries`` as list of dicts with ``name`` (or ``CurrencyName``) and optional
    ``display_order`` / ``DisplayOrder``. Or pass ``count`` alone for empty rows.
    """
    actor = lookup.require_actor(actor_path)
    script = _verse_script(actor)
    prop = _mangled_name("CurrencyConfigs", script)
    name_prop = _mangled_name("CurrencyName", script)
    order_prop = _mangled_name("DisplayOrder", script)
    built: List[Any] = []
    specs = entries if entries is not None else [{} for _ in range(max(0, int(count)))]
    with unreal.ScopedEditorTransaction("MCP Set Currency Configs"):
        for i, spec in enumerate(specs):
            entry = _new_struct_instance("currency_config")
            if spec:
                name = spec.get("name") or spec.get("CurrencyName") or f"Currency{i}"
                entry.set_editor_property(name_prop, str(name))
                order = spec.get("display_order", spec.get("DisplayOrder", i))
                entry.set_editor_property(order_prop, int(order))
            built.append(entry)
        script.set_editor_property(prop, built)
        script.modify()
        actor.modify()
    names = []
    for row in script.get_editor_property(prop):
        try:
            names.append(str(row.get_editor_property(name_prop)))
        except Exception:
            names.append("")
    return {
        "actor_path": actor.get_path_name(),
        "count": len(built),
        "currency_names": names,
        "ok": len(script.get_editor_property(prop)) == len(built),
    }


def _spawn_pads_for_manager(manager: unreal.Actor) -> List[unreal.Actor]:
    """Return spawn pads parented to *manager* (outliner children), sorted by label."""
    pads = []
    mgr_path = manager.get_path_name()
    for a in lookup.actor_list():
        if not is_live(a) or a.get_class().get_name() != "BP_Creative_Player_Spawner_Prop_C":
            continue
        parent = a.get_attach_parent_actor()
        if parent and parent.get_path_name() == mgr_path:
            pads.append(a)
    pads.sort(key=lambda p: p.get_actor_label())
    return pads


def wire_player_spawners(
    manager_path: str,
    spawn_pad_paths: Optional[List[str]] = None,
) -> dict:
    """Wire ``AllPlayerSpawners`` wrappers to spawn pads via ``SavedActor``.

    If ``spawn_pad_paths`` is omitted, uses ``BP_Creative_Player_Spawner_Prop_C``
    actors attached under the manager in the outliner.
    """
    from listener.script_property_overrides import apply_spawner_links

    actor = lookup.require_actor(manager_path)
    if spawn_pad_paths:
        pads = [lookup.require_actor(p) for p in spawn_pad_paths]
    else:
        pads = _spawn_pads_for_manager(actor)
    if not pads:
        raise ValueError(
            f"No spawn pads found for {actor.get_actor_label()!r}. "
            "Attach spawn pads under the manager or pass spawn_pad_paths."
        )

    with unreal.ScopedEditorTransaction("MCP Wire Player Spawners"):
        result = apply_spawner_links(actor, pads)
    result["actor_path"] = actor.get_path_name()
    result["spawn_pad_labels"] = [p.get_actor_label() for p in pads]
    return result


def set_verse_texture_icon(
    actor_path: str,
    icon_field: str,
    texture_path: str,
    array_field: str = "",
    entry_index: int = 0,
) -> dict:
    """Set a ``?texture`` / ``Assets_texture`` icon on a Verse device or struct row.

    For icons inside an array (``CurrencyConfigs``, ``Levels``), pass
    ``array_field`` and ``entry_index``. Example: currency row 0 icon on wallet.

        set_verse_texture_icon("WalletDevice", "CurrencyIcon", "T_GoldIcon",
                             array_field="CurrencyConfigs", entry_index=0)
    """
    from listener.script_property_overrides import apply_texture_icon

    actor = lookup.require_actor(actor_path)
    script = _verse_script(actor)
    owner = script
    arr_mangled = None
    if array_field:
        arr_mangled = _mangled_name(array_field)
        entries = script.get_editor_property(arr_mangled)
        idx = int(entry_index)
        if idx < 0 or idx >= len(entries):
            raise ValueError(
                f"{array_field}[{idx}] out of range (len={len(entries)}) on "
                f"{actor.get_actor_label()!r}"
            )
        owner = entries[idx]

    with unreal.ScopedEditorTransaction(f"MCP Set Verse Texture {icon_field}"):
        icon_prop = _mangled_name(icon_field)
        result = apply_texture_icon(owner, icon_prop, texture_path, array_prop=arr_mangled)
        script.modify()
        actor.modify()

    result["actor_path"] = actor.get_path_name()
    result["array_field"] = array_field or None
    result["entry_index"] = entry_index if array_field else None
    return result


def bulk_set_verse_editables(actor_path: str, properties: Dict[str, Any]) -> dict:
    results = {}
    for field, spec in properties.items():
        if isinstance(spec, str):
            results[field] = set_verse_editable(actor_path, field, target_path=spec)
        elif isinstance(spec, dict) and "target_path" in spec:
            results[field] = set_verse_editable(
                actor_path, field, target_path=spec["target_path"]
            )
        else:
            results[field] = set_verse_editable(actor_path, field, value=spec)
    return {
        "actor_path": actor_path,
        "results": results,
        "success_count": sum(1 for r in results.values() if r.get("ok")),
    }


def _is_game_asset_path(value: str) -> bool:
    return isinstance(value, str) and value.startswith("/Game/")


def _wire_one_field(
    actor_path: str,
    actor: unreal.Actor,
    field: str,
    value: Any,
) -> dict:
    """Route one wiring entry using get_verse_editables-style hints."""
    if field == "CurrencyConfigs" and isinstance(value, list):
        return set_currency_config_entries(actor_path, entries=value)

    if field == "AllPlayerSpawners" or (
        isinstance(value, dict) and "spawn_pad_paths" in value
    ):
        pads = None
        if isinstance(value, dict):
            pads = value.get("spawn_pad_paths")
        return wire_player_spawners(actor_path, spawn_pad_paths=pads)

    if isinstance(value, dict) and "texture_path" in value:
        return set_verse_texture_icon(
            actor_path,
            value.get("icon_field", field),
            value["texture_path"],
            array_field=value.get("array_field", ""),
            entry_index=int(value.get("entry_index", 0)),
        )

    hint = _wiring_hint(actor, field)
    tool = hint.get("tool")

    if tool == "wire_verse_device_ref":
        if not isinstance(value, str):
            raise ValueError(f"{field!r}: expected target label string, got {type(value).__name__}")
        return wire_verse_device_ref(actor_path, field, value)

    if tool == "wire_verse_prop_assets":
        paths = value if isinstance(value, list) else [value]
        if not all(isinstance(p, str) for p in paths):
            raise ValueError(f"{field!r}: prop asset paths must be strings")
        return wire_verse_prop_assets(actor_path, field, paths)

    if tool == "wire_verse_device_array":
        paths = value if isinstance(value, list) else [value]
        if not isinstance(paths, list) or not paths:
            raise ValueError(f"{field!r}: expected non-empty list of target labels or asset paths")
        if all(_is_game_asset_path(p) for p in paths):
            return wire_verse_prop_assets(actor_path, field, paths)
        return wire_verse_device_array(actor_path, field, paths)

    if tool == "set_verse_editable":
        if isinstance(value, str):
            return set_verse_editable(actor_path, field, target_path=value)
        if isinstance(value, dict):
            if "target_path" in value:
                return set_verse_editable(actor_path, field, target_path=value["target_path"])
            if "value" in value:
                return set_verse_editable(actor_path, field, value=value["value"])
        return set_verse_editable(actor_path, field, value=value)

    if isinstance(value, list) and value and all(_is_game_asset_path(p) for p in value):
        return wire_verse_prop_assets(actor_path, field, value)

    if isinstance(value, list):
        return wire_verse_device_array(actor_path, field, value)

    if isinstance(value, str):
        if lookup.find_actor(value) is not None:
            target = lookup.require_actor(value)
            if target.get_class().get_name() == "VerseDevice_C":
                return set_verse_editable(actor_path, field, target_path=value)
            return wire_verse_device_ref(actor_path, field, value)
        if _is_game_asset_path(value):
            return wire_verse_prop_assets(actor_path, field, [value])

    raise ValueError(
        f"Cannot infer wiring tool for {field!r} (hint={tool!r}). "
        "Call get_verse_editables for wiring.tool guidance."
    )


def bulk_wire_verse_device(
    actor_path: str,
    wiring: Dict[str, Any],
    skip_missing: bool = False,
    save_level: bool = False,
    verify: bool = False,
) -> dict:
    """Wire many @editable fields on one Verse device in a single call."""
    actor = lookup.require_actor(actor_path)
    results: Dict[str, dict] = {}
    errors: Dict[str, str] = {}

    for field, value in wiring.items():
        try:
            if value is None:
                raise ValueError("wiring value cannot be null")
            if field == "AllPlayerSpawners" and value == {}:
                pass
            elif isinstance(value, str) and not _is_game_asset_path(value):
                if lookup.find_actor(value) is None:
                    msg = f"Target actor not found: {value!r}"
                    if skip_missing:
                        errors[field] = msg
                        results[field] = {"ok": False, "skipped": True, "error": msg}
                        continue
                    raise ValueError(msg)
            out = _wire_one_field(actor_path, actor, field, value)
            hint_tool = _wiring_hint(actor, field).get("tool")
            if field == "AllPlayerSpawners" or (
                isinstance(value, dict) and "spawn_pad_paths" in value
            ):
                hint_tool = "wire_player_spawners"
            elif isinstance(value, dict) and "texture_path" in value:
                hint_tool = "set_verse_texture_icon"
            out["tool_used"] = hint_tool or out.get("tool_used")
            results[field] = out
        except Exception as exc:
            if skip_missing:
                errors[field] = str(exc)
                results[field] = {"ok": False, "skipped": True, "error": str(exc)}
            else:
                raise

    out: dict = {
        "actor_path": actor.get_path_name(),
        "label": actor.get_actor_label(),
        "results": results,
        "success_count": sum(1 for r in results.values() if r.get("ok")),
        "error_count": len(errors),
        "errors": errors or None,
    }

    if save_level:
        request_level_save()
        out["save"] = "scheduled"

    if verify:
        out["verify"] = get_verse_editables(actor_path)

    return out


def setup_verse_device(
    asset_path: str,
    label: str,
    wiring: Dict[str, Any],
    location: Optional[List[float]] = None,
    folder: str = "",
    spawn_if_exists: str = "skip",
    save_level: bool = True,
    verify: bool = True,
    skip_missing: bool = False,
) -> dict:
    """Spawn (if needed), label, and bulk-wire any Verse device class."""
    from listener.dispatch import dispatch
    from listener.handlers.actors import _load_placeable

    if _load_placeable(asset_path) is None:
        raise ValueError(
            f"Verse asset/class not found: {asset_path}. "
            "Run workspace_compile_verse yourself (never ask the user to build in UEFN), "
            "then search_assets(search='<class_name>', directory='/_Verse') for the real "
            "compiled path, then retry setup_verse_device with that path."
        )

    existing = lookup.find_actor(label)
    spawned = False
    actor_path = label

    if existing is not None:
        mode = (spawn_if_exists or "skip").lower()
        if mode == "error":
            raise ValueError(f"Actor with label {label!r} already exists")
        if mode == "replace":
            dispatch("delete_actors", {"actor_paths": [label]})
            existing = None
        elif mode != "skip":
            raise ValueError(f"spawn_if_exists must be skip, error, or replace — got {spawn_if_exists!r}")

    if existing is None:
        spawn_params: Dict[str, Any] = {"asset_path": asset_path, "select": False}
        if location:
            spawn_params["location"] = location
        spawn_result = dispatch("spawn_actor", spawn_params)
        actor_info = spawn_result.get("actor") or {}
        actor_path = actor_info.get("path") or actor_info.get("label") or label
        actor = lookup.require_actor(actor_path)
        actor.set_actor_label(label)
        if folder:
            actor.set_folder_path(unreal.Name(folder))
        actor.modify()
        lookup.invalidate()
        actor_path = label
        spawned = True
    else:
        if existing.get_actor_label() != label:
            existing.set_actor_label(label)
            lookup.invalidate()

    wire_result = bulk_wire_verse_device(
        actor_path,
        wiring,
        skip_missing=skip_missing,
        save_level=save_level,
        verify=verify,
    )

    return {
        "asset_path": asset_path,
        "label": label,
        "spawned": spawned,
        "folder": folder or None,
        "location": location,
        "spawn_if_exists": spawn_if_exists,
        "wiring": wire_result,
        "ok": wire_result.get("success_count", 0) == len(wiring) if wiring else True,
    }
