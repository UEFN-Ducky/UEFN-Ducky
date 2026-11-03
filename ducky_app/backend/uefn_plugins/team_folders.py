"""Team folders (a ``team_folders`` entry in plugin.json) sync with Ducky Account's engine.
This module path stays so every caller and plugin that imports it keeps working: each name
forwards to :data:`backend.uefn_plugins.team_engine.team_folders` (a safe default without
the engine). What plugin authors and installs rely on stays here, real: reading and checking
a manifest's declarations, and the project helpers plugins use.
"""

from __future__ import annotations

import hashlib
import os
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from backend.uefn_plugins.team_engine import team_folders as _area

TOKENS = ("{LOCALAPPDATA}", "{PROJECT}")
UNITS = ("*", ".", "file")
LOCKS = ("unit", "none")
AFTER_PULL = ("notify", "reload")
ON_LOCKED = ("readonly", "quarantine")
FIELDS = ("id", "root", "unit", "lock", "last", "ignore", "after_pull", "on_locked", "merge")
MAX_FOLDERS = 8
MAX_GLOBS = 64
MAX_ROOT = 200
APP_DIR = "UEFN-Ducky"
# The app's own folders under %LOCALAPPDATA%/UEFN-Ducky: never a plugin's to declare.
CORE_DIRS = frozenset({
    "scopes", "uefn_plugins", "skill_packs", "ai_plugins", "mcp_plugins", "snapshots", "legacy", "listener",
    "backups", "exports", "imports", "plugin_local", "coding_agents", "webview2_browser", "chats", "workspace",
    "ducky.db", "ducky.db-wal", "ducky.db-shm", "uefn_plugin_cache", "uefn_plugin_prefs",
})
_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")
_BAD_SEGMENT = re.compile(r'[<>:"\\|?*\x00-\x1f]')
_LONG = "\\\\?\\"
_LONG_UNC = "\\\\?\\UNC\\"


# --------------------------------------------------------------------------- declarations


@dataclass(frozen=True)
class Folder:
    id: str
    root: str
    unit: str = "*"
    lock: str = "unit"
    last: tuple[str, ...] = ()
    ignore: tuple[str, ...] = ()
    after_pull: str = "notify"
    on_locked: str = "readonly"
    merge: tuple[str, ...] = ()

    @property
    def project(self) -> bool:
        return self.root.startswith("{PROJECT}/")


def _root_errors(root: Any, where: str) -> list[str]:
    usage = f'{where} "root" must start with {{LOCALAPPDATA}}/UEFN-Ducky/ or {{PROJECT}}/, e.g. ' \
            '"{LOCALAPPDATA}/UEFN-Ducky/<plugin>/data".'
    if not isinstance(root, str) or not root:
        return [usage]
    token, sep, rest = root.partition("/")
    if token not in TOKENS or not sep or not rest:
        return [usage]
    if len(root) > MAX_ROOT:
        return [f'{where} "root" is over {MAX_ROOT} characters: use a shorter path.']
    segments = rest.split("/")
    for seg in segments:
        if not seg or seg in (".", "..") or _BAD_SEGMENT.search(seg) or seg != seg.rstrip(" ."):
            return [f'{where} "root" {root!r} has a bad part {seg!r}: use plain folder names separated by /, '
                    'without .. or \\.']
    if token == "{LOCALAPPDATA}":
        if segments[0].casefold() != APP_DIR.casefold() or len(segments) < 2:
            return [f'{where} "root" {root!r} must be inside {{LOCALAPPDATA}}/UEFN-Ducky/, e.g. '
                    '"{LOCALAPPDATA}/UEFN-Ducky/<plugin>/data".']
        if segments[1].casefold() in CORE_DIRS:
            return [f'{where} "root" {root!r} is one of the app\'s own folders: use a folder of the plugin\'s own, '
                    'e.g. "{LOCALAPPDATA}/UEFN-Ducky/<plugin>/data".']
    return []


def _glob_errors(name: str, value: Any, where: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list) or not all(isinstance(g, str) for g in value):
        return [f'{where} "{name}" must be a list of globs, e.g. ["*/history.json", "**/*.tmp"].']
    if len(value) > MAX_GLOBS:
        return [f'{where} "{name}" has {len(value)} globs: {MAX_GLOBS} at most.']
    for glob in value:
        if not glob or glob.startswith("/") or "\\" in glob or ".." in glob.split("/") or len(glob) > 200:
            return [f'{where} "{name}" glob {glob!r} isn\'t a relative glob: use / between parts, no .. and no '
                    'leading /, e.g. "*/history.json" or "**/*.tmp".']
    return []


def _choice_error(entry: dict[str, Any], name: str, allowed: tuple[str, ...], where: str) -> list[str]:
    if name not in entry or entry[name] in allowed:
        return []
    return [f'{where} "{name}" is {entry[name]!r}: use {" or ".join(repr(a).replace(chr(39), chr(34)) for a in allowed)}.']


def _entry(entry: Any, index: int) -> tuple[list[str], Folder | None]:
    if not isinstance(entry, dict):
        return [f'plugin.json "team_folders" entry {index + 1} must be an object with "id", "root" and "unit".'], None
    fid = entry.get("id")
    where = f'plugin.json "team_folders" {fid!r}' if isinstance(fid, str) and fid else \
        f'plugin.json "team_folders" entry {index + 1}'
    errors: list[str] = []
    unknown = sorted(str(k) for k in entry if k not in FIELDS)
    if unknown:
        errors.append(f'{where} has unknown {", ".join(repr(k) for k in unknown)}: use {", ".join(FIELDS)}.')
    if not isinstance(fid, str) or not _ID_RE.match(fid):
        errors.append(f'{where} "id" must be 1-32 of a-z, 0-9, _ and -, starting with a letter or digit.')
    errors += _root_errors(entry.get("root"), where)
    if entry.get("unit") not in UNITS:
        errors.append(f'{where} "unit" is {entry.get("unit")!r}: use "*" (each top-level folder or file), "." '
                      '(the whole folder) or "file" (every file).')
    errors += _choice_error(entry, "lock", LOCKS, where)
    errors += _choice_error(entry, "after_pull", AFTER_PULL, where)
    errors += _choice_error(entry, "on_locked", ON_LOCKED, where)
    for name in ("last", "ignore", "merge"):
        errors += _glob_errors(name, entry.get(name), where)
    if errors:
        return errors, None
    return [], Folder(
        id=fid, root=entry["root"], unit=entry["unit"], lock=entry.get("lock", "unit"),
        last=tuple(entry.get("last") or ()), ignore=tuple(entry.get("ignore") or ()),
        after_pull=entry.get("after_pull", "notify"), on_locked=entry.get("on_locked", "readonly"),
        merge=tuple(entry.get("merge") or ()),
    )


def _overlap(a: str, b: str) -> bool:
    x, y = a.casefold().rstrip("/") + "/", b.casefold().rstrip("/") + "/"
    return x.startswith(y) or y.startswith(x)


def _check(manifest: dict[str, Any]) -> tuple[list[str], list[Folder]]:
    if "team_folders" not in manifest:
        return [], []
    raw = manifest.get("team_folders")
    if not isinstance(raw, list):
        return ['plugin.json "team_folders" must be a list of folders: [{"id": "scenes", "root": '
                '"{LOCALAPPDATA}/UEFN-Ducky/<plugin>/scenes", "unit": "*"}].'], []
    errors: list[str] = []
    if len(raw) > MAX_FOLDERS:
        errors.append(f'plugin.json "team_folders" has {len(raw)} folders: {MAX_FOLDERS} at most.')
    good: list[Folder] = []
    for i, entry in enumerate(raw[:MAX_FOLDERS]):
        errs, folder = _entry(entry, i)
        errors += errs
        if folder is None:
            continue
        if any(f.id == folder.id for f in good):
            errors.append(f'plugin.json "team_folders" has two folders with "id" {folder.id!r}: give each its own.')
            continue
        clash = next((f for f in good if _overlap(f.root, folder.root)), None)
        if clash is not None:
            errors.append(f'plugin.json "team_folders" {folder.id!r} and {clash.id!r} overlap: one root is inside '
                          'the other. Declare each folder once.')
            continue
        good.append(folder)
    if raw and manifest.get("data_sharing") is False:
        errors.append('plugin.json has "team_folders" but "data_sharing": false: nothing is shared, so remove one.')
        good = []
    return errors, good


def declaration_errors(manifest: dict[str, Any]) -> list[str]:
    """What is wrong with a manifest's ``team_folders``, each with the fix."""
    return _check(manifest)[0]


def declarations(manifest: dict[str, Any]) -> list[Folder]:
    """The valid folders of a manifest (none while ``data_sharing`` is false)."""
    return _check(manifest)[1]


# --------------------------------------------------------------------------- project helpers plugins use


def norm_name(name: str) -> str:
    """How names compare and hash: NFC, then casefolded."""
    return unicodedata.normalize("NFC", str(name)).casefold()


def _plain(path: Any) -> str:
    s = os.fspath(path)
    if s.startswith(_LONG_UNC):
        return "\\\\" + s[len(_LONG_UNC):]
    if s.startswith(_LONG):
        return s[len(_LONG):]
    return s


def _key(path: Any) -> str:
    return os.path.normcase(os.path.normpath(_plain(path)))


def current_project_root() -> str:
    """The open project's folder ("" when none)."""
    try:
        from frontend.settings import PanelSettings

        return str(PanelSettings.load().uefn_project_root or "").strip()
    except Exception:  # noqa: BLE001 — no project then
        return ""


def project_id_for(project_root: str) -> str:
    """The id a project's folder copies are kept under: the island's content mount (the
    GUID core matches projects by, else its name), made safe as a folder name."""
    raw = ""
    try:
        from frontend.project_kind import island_mount_name

        raw = island_mount_name(project_root)
    except Exception:  # noqa: BLE001
        raw = ""
    raw = raw or Path(str(project_root)).name
    safe = re.sub(r"[^a-z0-9._-]", "_", norm_name(raw)).strip("._")[:64]
    return safe or hashlib.sha256(_key(project_root).encode("utf-8")).hexdigest()[:16]


def __getattr__(name: str) -> Any:
    return getattr(_area, name)
