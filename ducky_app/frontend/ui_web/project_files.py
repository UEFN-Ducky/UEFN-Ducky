"""List and open files under the active UEFN project root."""

from __future__ import annotations

import hashlib
import os
import shutil
import tempfile
import time
import uuid
from pathlib import Path

from frontend.deploy import resolve_uefn_project_root
from frontend.settings import PanelSettings
from frontend.ui_web.file_kinds import (
    BINARY_FILE_SUFFIXES,
    EDITABLE_TEXT_SUFFIXES,
    IMAGE_SUFFIXES,
    classify_project_file,
    is_binary_file_name as _kinds_is_binary_file_name,
    is_text_exempt_from_nul_sniff,
    should_show_dot_entry,
)
from frontend.ui_web.verse_editor.lsp.verse_workspace import discover_verse_workspace

_SKIP_DIR_NAMES = frozenset(
    {
        ".git",
        ".svn",
        "__pycache__",
        "node_modules",
        ".vite",
        "dist",
        "Intermediate",
        "Saved",
        "DerivedDataCache",
        "Binaries",
    }
)

# Hidden from the sidebar unless the user enables "Show hidden project files".
_HIDDEN_DIR_NAMES = frozenset(
    {
        "__ExternalActors__",
        "__ExternalObjects__",
        "Collections",
        "Developers",
    }
)

_HIDDEN_FILE_NAMES = frozenset(
    {
        "GameFeatureData.uasset",
    }
)

_LOCKED_RELATIVE_PATHS = frozenset(
    {
        "content/python/init_unreal.py",
    }
)

_BINARY_PREVIEW_MAX_BYTES = 256 * 1024

# Binary suffixes (images + unreal + archives + …) — never opened as text.
# Unknown extensions are still NUL-sniffed in read_project_file.
_BINARY_FILE_SUFFIXES = BINARY_FILE_SUFFIXES
_EDITABLE_TEXT_SUFFIXES = EDITABLE_TEXT_SUFFIXES
_IMAGE_FILE_SUFFIXES = IMAGE_SUFFIXES

# Hard cap for opening a file as text in the panel. Any larger payload has to cross the
# pywebview JSON bridge as one string and be parsed on the renderer main thread, which
# freezes the whole app (a dropped-in .exe read as UTF-8 froze the panel for minutes).
_TEXT_OPEN_MAX_BYTES = 8 * 1024 * 1024

CONTENT_DIR = "Content"
WORKSPACE_ROOTS_PATH = "__roots__"
WS_PATH_PREFIX = "ws:"
ABS_PATH_PREFIX = "abs:"
# Like abs:, but NOT gated to workspace folders. Only ever produced when the user
# explicitly drags a file into the panel from Explorer, so the drop authorizes the
# read. Opened read-only; never written.
EXT_PATH_PREFIX = "ext:"

_DEFAULT_VERSE_FILE = "using { /Verse.org/Simulation }\n\n"

_workspace_folders_cache: dict[str, list[dict[str, str]]] = {}


def _project_root() -> Path:
    raw = PanelSettings.load().uefn_project_root.strip()
    if not raw:
        raise ValueError("No project selected.")
    return resolve_uefn_project_root(Path(raw))


def _invalidate_workspace_folders_cache() -> None:
    _workspace_folders_cache.clear()


def _workspace_folders() -> list[dict[str, str]]:
    root = _project_root().resolve()
    key = str(root)
    if key in _workspace_folders_cache:
        return _workspace_folders_cache[key]
    ws = discover_verse_workspace(str(root))
    folders = ws.get("workspace_folders") or []
    if not isinstance(folders, list):
        folders = []
    out: list[dict[str, str]] = []
    for item in folders:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        path = str(item.get("path") or "").strip()
        if not path:
            continue
        out.append({"name": name or Path(path).name, "path": path})
    _workspace_folders_cache[key] = out
    return out


def _workspace_folder_paths() -> list[Path]:
    return [Path(f["path"]).resolve() for f in _workspace_folders()]


def _workspace_folder_index(path: Path) -> int | None:
    resolved = path.resolve()
    for i, folder in enumerate(_workspace_folders()):
        try:
            if Path(folder["path"]).resolve() == resolved:
                return i
        except OSError:
            continue
    return None


def _is_content_folder_path(folder_path: Path) -> bool:
    try:
        return folder_path.resolve() == _content_dir()
    except ValueError:
        return False


def _is_workspace_locked_path(relative_path: str) -> bool:
    """True for abs: paths and any path outside Content/."""
    rel = (relative_path or "").strip().replace("\\", "/")
    if rel.lower().startswith(ABS_PATH_PREFIX.lower()):
        return True
    if rel.lower().startswith(WS_PATH_PREFIX.lower()):
        return True
    if rel.lower() in {"", WORKSPACE_ROOTS_PATH.lower()}:
        return True
    return not rel.lower().startswith(f"{CONTENT_DIR.lower()}/") and rel.lower() != CONTENT_DIR.lower()


def _encode_abs_path(path: Path) -> str:
    return f"{ABS_PATH_PREFIX}{str(path.resolve()).replace(chr(92), '/')}"


def _decode_abs_path(encoded: str) -> Path:
    raw = (encoded or "").strip().replace("\\", "/")
    if not raw.lower().startswith(ABS_PATH_PREFIX.lower()):
        raise ValueError(f"Not an absolute workspace path: {encoded!r}")
    abs_part = raw[len(ABS_PATH_PREFIX) :]
    if not abs_part:
        raise ValueError(f"Empty absolute workspace path: {encoded!r}")
    normalized = abs_part.replace("\\", "/").rstrip("/")
    if len(normalized.split("/")) <= 1:
        raise ValueError(f"Invalid workspace path (not under a folder): {encoded!r}")
    target = Path(abs_part).resolve()
    if not _is_under_any_workspace_folder(target):
        raise ValueError(f"Path escapes workspace folders: {encoded!r}")
    return target


def _is_abs_encoded_path(relative_path: str) -> bool:
    return (relative_path or "").strip().replace("\\", "/").lower().startswith(ABS_PATH_PREFIX.lower())


def _encode_ext_path(path: Path) -> str:
    return f"{EXT_PATH_PREFIX}{str(path.resolve()).replace(chr(92), '/')}"


def _decode_ext_path(encoded: str) -> Path:
    """Decode an ext: key into an absolute path. Unlike abs:, this is NOT gated to
    workspace folders — an ext: key only ever comes from a file the user dragged into
    the panel, so the drop itself is the authorization to read it."""
    raw = (encoded or "").strip().replace("\\", "/")
    if not raw.lower().startswith(EXT_PATH_PREFIX.lower()):
        raise ValueError(f"Not an external file path: {encoded!r}")
    abs_part = raw[len(EXT_PATH_PREFIX) :]
    if not abs_part:
        raise ValueError(f"Empty external file path: {encoded!r}")
    return Path(abs_part).resolve()


def _is_ext_encoded_path(relative_path: str) -> bool:
    return (relative_path or "").strip().replace("\\", "/").lower().startswith(EXT_PATH_PREFIX.lower())


def _is_ws_encoded_path(relative_path: str) -> bool:
    return (relative_path or "").strip().replace("\\", "/").lower().startswith(WS_PATH_PREFIX.lower())


def _parse_ws_index(relative_path: str) -> int:
    raw = (relative_path or "").strip().replace("\\", "/")
    if not raw.lower().startswith(WS_PATH_PREFIX.lower()):
        raise ValueError(f"Not a workspace root path: {relative_path!r}")
    index_text = raw[len(WS_PATH_PREFIX) :].strip("/")
    if not index_text.isdigit():
        raise ValueError(f"Invalid workspace root index: {relative_path!r}")
    index = int(index_text)
    folders = _workspace_folders()
    if index < 0 or index >= len(folders):
        raise ValueError(f"Workspace root index out of range: {relative_path!r}")
    return index


def _norm_path_key(path: Path | str) -> str:
    """Comparable path key — case-insensitive on Windows (BuiltIn vs builtin)."""
    text = str(path)
    if os.name == "nt":
        return os.path.normcase(os.path.normpath(text))
    return os.path.normpath(text)


def _is_path_under(child: Path, ancestor: Path) -> bool:
    """True if child is ancestor or under it (Windows-safe casing)."""
    try:
        common = os.path.commonpath([str(child.resolve()), str(ancestor.resolve())])
    except (ValueError, OSError):
        return False
    return _norm_path_key(common) == _norm_path_key(ancestor)


def _is_under_any_workspace_folder(target: Path) -> bool:
    resolved = target.resolve()
    for folder_path in _workspace_folder_paths():
        if _is_path_under(resolved, folder_path):
            return True
    return False


def _entry_path_for_target(target: Path) -> str:
    """Content-relative path when under Content; otherwise abs: encoding."""
    root = _project_root().resolve()
    try:
        if _is_under_content(target):
            return str(target.resolve().relative_to(root)).replace("\\", "/")
    except ValueError:
        pass
    if not _is_under_any_workspace_folder(target):
        raise ValueError(f"Path is outside workspace: {target}")
    return _encode_abs_path(target)


def resolve_project_file_path(relative_path: str) -> str:
    """Absolute filesystem path for a panel tree path (Content/, ws:, or abs:)."""
    return str(_resolve_file_path(relative_path))


def _resolve_file_path(relative_path: str) -> Path:
    """Resolve Content/, ws:, or abs: encoded paths."""
    rel = (relative_path or "").strip().replace("\\", "/").strip("/")
    if not rel or rel.lower() == WORKSPACE_ROOTS_PATH.lower():
        raise ValueError("Use list_workspace_roots for workspace roots.")
    if _is_ext_encoded_path(rel):
        return _decode_ext_path(rel)
    if _is_abs_encoded_path(rel):
        target = _decode_abs_path(rel)
        if not _is_under_any_workspace_folder(target):
            raise ValueError(f"Path escapes workspace folders: {relative_path!r}")
        return target
    if _is_ws_encoded_path(rel):
        index = _parse_ws_index(rel)
        return Path(_workspace_folders()[index]["path"]).resolve()
    return _resolve_relative(rel)


def _require_writable_content_path(relative_path: str) -> None:
    if _is_workspace_locked_path(relative_path):
        raise ValueError(f"Locked workspace file cannot be modified: {relative_path}")
    _require_not_digest(relative_path)
    target = _resolve_relative(relative_path)
    _require_under_content(target, relative_path)


def list_workspace_roots() -> list[dict[str, object]]:
    """UEFN multi-root workspace folders (VS Code layout)."""
    out: list[dict[str, object]] = []
    for i, folder in enumerate(_workspace_folders()):
        folder_path = Path(folder["path"])
        try:
            resolved = str(folder_path.resolve())
        except OSError:
            resolved = folder["path"]
        read_only = not _is_content_folder_path(folder_path)
        out.append(
            {
                "id": f"{WS_PATH_PREFIX}{i}",
                "name": folder["name"],
                "path": resolved,
                "read_only": read_only,
            }
        )
    return out


def _resolve_relative(relative_path: str) -> Path:
    root = _project_root()
    rel = (relative_path or "").strip().replace("\\", "/").strip("/")
    target = (root / rel).resolve() if rel else root.resolve()
    root_r = root.resolve()
    if not _is_path_under(target, root_r):
        raise ValueError(f"Path escapes project root: {relative_path!r}")
    return target


def _content_dir() -> Path:
    root = _project_root()
    content = (root / CONTENT_DIR).resolve()
    if not content.is_dir():
        raise ValueError("Project has no Content folder.")
    return content


def _is_under_content(target: Path) -> bool:
    try:
        return _is_path_under(target, _content_dir())
    except ValueError:
        return False


def _require_under_content(target: Path, relative_path: str) -> None:
    if not _is_under_content(target):
        raise ValueError(f"Path is outside Content: {relative_path!r}")


def _validate_entry_name(name: str) -> str:
    cleaned = name.strip().replace("\\", "/")
    if not cleaned or "/" in cleaned or cleaned in {".", ".."}:
        raise ValueError(f"Invalid name: {name!r}")
    return cleaned


def _join_content_path(parent_relative: str, name: str) -> str:
    parent = (parent_relative or CONTENT_DIR).strip().replace("\\", "/").strip("/")
    safe_name = _validate_entry_name(name)
    return f"{parent}/{safe_name}" if parent else safe_name


def _norm_relative_path(relative_path: str) -> str:
    return (relative_path or "").strip().replace("\\", "/").strip("/").lower()


def _show_hidden_project_files() -> bool:
    return PanelSettings.load().show_hidden_project_files


def is_locked_project_file(relative_path: str) -> bool:
    return _norm_relative_path(relative_path) in _LOCKED_RELATIVE_PATHS


def _require_not_locked(relative_path: str) -> None:
    if is_locked_project_file(relative_path):
        raise ValueError(f"File is locked and cannot be modified: {relative_path}")


def _require_not_digest(relative_path: str) -> None:
    """UEFN digests are Epic-generated — never create/edit/delete via the panel."""
    from backend.tools.verse.verse_digests import require_not_digest_path

    require_not_digest_path(relative_path)


def _require_writable_mutation(relative_path: str) -> None:
    _require_writable_content_path(relative_path)
    _require_not_locked(relative_path)
    _require_not_digest(relative_path)


def _is_uefn_engine_file(name: str) -> bool:
    """UEFN boilerplate under Content — any project name, not a specific prefix."""
    if name in _HIDDEN_FILE_NAMES:
        return True
    suffix = Path(name).suffix.lower()
    if suffix == ".umap":
        return True
    # e.g. MyGame_DefaultHLODLayer.uasset
    if "_Default" in Path(name).stem:
        return True
    return False


def _is_hidden_tree_entry(name: str, is_dir: bool) -> bool:
    if _show_hidden_project_files():
        return False
    if is_dir and name in _HIDDEN_DIR_NAMES:
        return True
    if not is_dir and _is_uefn_engine_file(name):
        return True
    return False


def _binary_file_preview(name: str, chunk: bytes, total_size: int) -> str:
    truncated = total_size > len(chunk)
    chunk = chunk[:_BINARY_PREVIEW_MAX_BYTES]
    lines = [
        f"# Binary file preview (read-only): {name}",
        f"# Size: {total_size:,} bytes"
        + (f" — showing first {len(chunk) // 1024} KB" if truncated else ""),
        "",
    ]
    for offset in range(0, len(chunk), 16):
        row = chunk[offset : offset + 16]
        hex_part = " ".join(f"{byte:02x}" for byte in row)
        ascii_part = "".join(chr(byte) if 32 <= byte < 127 else "." for byte in row)
        lines.append(f"{offset:08x}  {hex_part:<48}  {ascii_part}")
    if truncated:
        lines.append("")
        lines.append(f"# … {total_size - len(chunk):,} more bytes not shown")
    return "\n".join(lines)


def _should_skip(name: str, is_dir: bool) -> bool:
    # Dotfiles: always show common text names (.gitignore, …); other dots follow
    # show_hidden. .git / .svn stay hidden. Non-dot skip dirs (.git is also covered).
    if name.startswith("."):
        if not should_show_dot_entry(name, is_dir, show_hidden=_show_hidden_project_files()):
            return True
    if is_dir and name in _SKIP_DIR_NAMES:
        return True
    return False


def _include_in_content_tree(name: str, is_dir: bool) -> bool:
    if _should_skip(name, is_dir):
        return False
    return not _is_hidden_tree_entry(name, is_dir)


def _is_binary_file_name(name: str) -> bool:
    return _kinds_is_binary_file_name(name)


def is_editable_text_file(relative_path: str) -> bool:
    """True for panel-editable text files under Content (not binary or locked)."""
    if is_locked_project_file(relative_path):
        return False
    if _is_ext_encoded_path(relative_path):
        # External drops are editable in place only when classified as text.
        info = classify_project_file(relative_path)
        if info["kind"] != "text":
            return False
        # Extensionless / unknown: sniff on disk if present.
        try:
            target = _decode_ext_path(relative_path)
            if target.is_file() and Path(target.name).suffix == "":
                head = target.read_bytes()[:4096]
                return classify_project_file(relative_path, head=head)["kind"] == "text"
        except (ValueError, OSError):
            return bool(info["editable"])
        return True
    if _is_workspace_locked_path(relative_path):
        return False
    info = classify_project_file(relative_path)
    if info["kind"] != "text":
        return False
    # Extensionless Content files: sniff when the file exists.
    try:
        target = _resolve_relative(relative_path)
        if target.is_file() and not Path(target.name).suffix:
            head = target.read_bytes()[:4096]
            return classify_project_file(relative_path, head=head)["kind"] == "text"
    except (ValueError, OSError):
        pass
    name = Path((relative_path or "").strip().replace("\\", "/")).name
    suffix = Path(name).suffix.lower()
    if not suffix:
        # Creating new extensionless files isn't allowed via create_project_file
        # unless known text name; existing sniffed files handled above.
        from frontend.ui_web.file_kinds import is_known_text_filename

        return is_known_text_filename(name)
    return True


def include_in_workspace_search(name: str, is_dir: bool) -> bool:
    """Search walks the same folders as the sidebar but skips binary/image assets."""
    if not _include_in_content_tree(name, is_dir):
        return False
    if is_dir:
        return True
    info = classify_project_file(name)
    return info["kind"] == "text"


_file_paths_cache: dict[str, list[dict[str, str]]] = {}


def _invalidate_file_paths_cache() -> None:
    _file_paths_cache.clear()
    _invalidate_workspace_folders_cache()


def _list_directory_entries(target: Path, *, content_tree: bool) -> list[dict[str, str | bool]]:
    entries: list[dict[str, str | bool]] = []
    include = _include_in_content_tree if content_tree else _include_in_workspace_tree_entry
    try:
        names = sorted(os.listdir(target), key=lambda s: s.lower())
    except OSError as exc:
        raise ValueError(str(exc)) from exc
    for name in names:
        full = target / name
        is_dir = full.is_dir()
        if not include(name, is_dir):
            continue
        entry_rel = _entry_path_for_target(full)
        entries.append({"name": name, "path": entry_rel, "is_dir": is_dir})
    return entries


def _include_in_workspace_tree_entry(name: str, is_dir: bool) -> bool:
    if _should_skip(name, is_dir):
        return False
    return True


def list_project_file_paths() -> list[dict[str, str]]:
    """Flat list of project + workspace .verse files for sidebar filter / quick-open."""
    root = _project_root().resolve()
    root_key = f"{root}|hidden={_show_hidden_project_files()}|ws={len(_workspace_folders())}"
    if root_key in _file_paths_cache:
        return _file_paths_cache[root_key]

    out: list[dict[str, str]] = []
    content = _content_dir()
    for dirpath, dirnames, filenames in os.walk(content):
        dirnames[:] = sorted(
            (d for d in dirnames if _include_in_content_tree(d, True)),
            key=lambda s: s.lower(),
        )
        for name in sorted(filenames, key=lambda s: s.lower()):
            if not _include_in_content_tree(name, False):
                continue
            full = Path(dirpath) / name
            rel = str(full.relative_to(root)).replace("\\", "/")
            out.append({"path": rel, "name": name})

    for folder in _workspace_folders():
        folder_path = Path(folder["path"])
        if not folder_path.is_dir():
            continue
        if _is_content_folder_path(folder_path):
            continue
        for dirpath, dirnames, filenames in os.walk(folder_path):
            dirnames[:] = sorted(
                (d for d in dirnames if _include_in_workspace_tree_entry(d, True)),
                key=lambda s: s.lower(),
            )
            for name in sorted(filenames, key=lambda s: s.lower()):
                if not name.lower().endswith(".verse"):
                    continue
                if not _include_in_workspace_tree_entry(name, False):
                    continue
                full = Path(dirpath) / name
                out.append({"path": _encode_abs_path(full), "name": name})

    _file_paths_cache[root_key] = out
    return out


def list_project_files(relative_path: str = "") -> dict[str, object]:
    """List workspace roots, Content tree, or a workspace folder directory."""
    rel = (relative_path or "").strip().replace("\\", "/").strip("/")

    if not rel or rel.lower() == WORKSPACE_ROOTS_PATH.lower():
        entries: list[dict[str, str | bool]] = []
        for i, folder in enumerate(_workspace_folders()):
            folder_path = Path(folder["path"])
            entries.append(
                {
                    "name": folder["name"],
                    "path": f"{WS_PATH_PREFIX}{i}",
                    "is_dir": folder_path.is_dir(),
                    "read_only": not _is_content_folder_path(folder_path),
                }
            )
        return {"path": WORKSPACE_ROOTS_PATH, "entries": entries}

    if _is_ws_encoded_path(rel):
        index = _parse_ws_index(rel)
        target = Path(_workspace_folders()[index]["path"]).resolve()
        if not target.is_dir():
            raise ValueError(f"Not a directory: {relative_path}")
        content_tree = _is_content_folder_path(target)
        entries = _list_directory_entries(target, content_tree=content_tree)
        return {"path": rel, "entries": entries}

    if _is_abs_encoded_path(rel):
        target = _decode_abs_path(rel)
        if not _is_under_any_workspace_folder(target):
            raise ValueError(f"Path escapes workspace folders: {relative_path!r}")
        if not target.is_dir():
            raise ValueError(f"Not a directory: {relative_path}")
        entries = _list_directory_entries(target, content_tree=False)
        return {"path": rel, "entries": entries}

    target = _resolve_relative(rel)
    _require_under_content(target, rel)
    if not target.is_dir():
        raise ValueError(f"Not a directory: {relative_path or '.'}")
    entries = _list_directory_entries(target, content_tree=True)
    rel_dir = rel
    return {"path": rel_dir, "entries": entries}


def stat_project_file(relative_path: str) -> dict[str, int | str | bool]:
    """Cheap fingerprint for external-change detection (mtime + size)."""
    target = _resolve_file_path(relative_path)
    if _is_ext_encoded_path(relative_path):
        rel = _encode_ext_path(target)
    elif _is_abs_encoded_path(relative_path):
        rel = _encode_abs_path(target)
    else:
        _require_under_content(target, relative_path)
        rel = str(target.relative_to(_project_root().resolve())).replace("\\", "/")
    if not target.is_file():
        return {"path": rel, "mtime_ns": 0, "size": 0, "exists": False}
    try:
        st = target.stat()
    except OSError as exc:
        raise ValueError(str(exc)) from exc
    return {"path": rel, "mtime_ns": int(st.st_mtime_ns), "size": int(st.st_size), "exists": True}


def _dir_fingerprint(target: Path) -> str:
    """One-level fingerprint: child count, newest child mtime, and a hash of visible names.

    Catches adds/removes (count + hash), renames (hash), and touches (mtime). A child
    dir's own mtime bumps when *its* children change, so a level-1 hash also flags edits
    one folder deeper — enough for the sidebar's expanded-folder watch.
    """
    names: list[str] = []
    max_mtime_ns = 0
    with os.scandir(target) as it:
        for entry in it:
            is_dir = entry.is_dir()
            if not _include_in_content_tree(entry.name, is_dir):
                continue
            names.append(entry.name)
            try:
                max_mtime_ns = max(max_mtime_ns, entry.stat().st_mtime_ns)
            except OSError:
                continue
    names_hash = hashlib.md5("\n".join(sorted(names)).encode("utf-8")).hexdigest()[:12]
    return f"{len(names)}:{max_mtime_ns}:{names_hash}"


def fingerprint_project_dirs(relative_paths: list[str]) -> dict[str, object]:
    """Cheap per-directory fingerprints for external-change detection of the sidebar tree.

    Only Content directories are fingerprinted. Anything else (workspace roots, abs: paths,
    missing, or non-directory) yields "" so a single bad path can't fail the whole poll.
    """
    out: dict[str, str] = {}
    for raw in relative_paths or []:
        rel = (raw or "").strip().replace("\\", "/").strip("/")
        if not rel or _is_workspace_locked_path(rel):
            out[raw] = ""
            continue
        try:
            target = _resolve_relative(rel)
            out[raw] = _dir_fingerprint(target) if _is_under_content(target) and target.is_dir() else ""
        except (ValueError, OSError):
            out[raw] = ""
    return {"fingerprints": out}


def read_project_file(relative_path: str) -> dict[str, str]:
    """Read a text file under Content or a locked workspace path; images/models get a
    media URL; other binaries return a hex preview."""
    from frontend.ui_web.project_media import build_model_media_urls, build_project_media_url

    target = _resolve_file_path(relative_path)
    if _is_ext_encoded_path(relative_path):
        rel = _encode_ext_path(target)
    elif _is_abs_encoded_path(relative_path):
        rel = _encode_abs_path(target)
    else:
        _require_under_content(target, relative_path)
        rel = str(target.relative_to(_project_root().resolve())).replace("\\", "/")
    if not target.is_file():
        raise ValueError(f"Not a file: {relative_path}")
    try:
        size = target.stat().st_size
        with target.open("rb") as fh:
            head = fh.read(_BINARY_PREVIEW_MAX_BYTES)
    except OSError as exc:
        raise ValueError(str(exc)) from exc

    info = classify_project_file(rel, head=head)
    kind = str(info["kind"])
    mime = str(info["mime"])

    if kind == "image":
        return {
            "path": rel,
            "content": "",
            "kind": "image",
            "mime": mime,
            "media_url": build_project_media_url(rel),
            "size": str(size),
        }

    if kind == "model":
        urls = build_model_media_urls(rel)
        return {
            "path": rel,
            "content": "",
            "kind": "model",
            "mime": mime,
            "media_url": urls["media_url"],
            "media_base_url": urls["media_base_url"],
            "media_filename": urls["media_filename"],
            "size": str(size),
        }

    if kind in {"audio", "video"}:
        return {
            "path": rel,
            "content": "",
            "kind": kind,
            "mime": mime,
            "media_url": build_project_media_url(rel),
            "size": str(size),
        }

    # Known-binary suffix OR NUL bytes in the head → hex preview built from the head only;
    # the file itself is never read whole (a .umap or dropped .exe can be hundreds of MB).
    # Editable-text suffixes / known text names are exempt from the sniff: they open in a
    # SAVABLE editor host, and a hex preview loaded there could be written back over the file.
    sniffed_binary = b"\x00" in head and not is_text_exempt_from_nul_sniff(target.name)
    if kind in {"binary", "unreal_asset"} or sniffed_binary:
        return {
            "path": rel,
            "content": _binary_file_preview(target.name, head, size),
            "binary_preview": "true",
            "kind": "unreal_asset" if kind == "unreal_asset" else "binary",
            "mime": mime,
        }
    if size > _TEXT_OPEN_MAX_BYTES:
        raise ValueError(
            f"File is too large to open in the panel: {size / (1024 * 1024):.1f} MB "
            f"(limit {_TEXT_OPEN_MAX_BYTES // (1024 * 1024)} MB): {relative_path}"
        )
    try:
        text = target.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        raise ValueError(str(exc)) from exc
    return {"path": rel, "content": text, "kind": "text", "mime": mime}


def write_external_file(encoded: str, content: str) -> dict[str, object]:
    """Save edits back to a dragged-in external file at its real on-disk location.

    ext: only, and deliberately ungated (the user opened this file by dropping it in,
    so editing it is allowed). Atomic temp-file swap so a failed write never truncates
    the original; bounded to the same text cap as reads. Images/binaries are rejected.
    """
    if not _is_ext_encoded_path(encoded):
        raise ValueError(f"Not an external file path: {encoded!r}")
    target = _decode_ext_path(encoded)
    if not target.parent.is_dir():
        raise ValueError(f"Folder no longer exists: {encoded}")
    # Reject images/binaries even for unknown extensions (sniff on disk).
    if target.is_file():
        try:
            head = target.read_bytes()[:4096]
        except OSError:
            head = None
        else:
            info = classify_project_file(encoded, head=head)
            if info["kind"] != "text":
                raise ValueError(f"File type cannot be saved in panel: {encoded}")
    if not is_editable_text_file(encoded):
        raise ValueError(f"File type cannot be saved in panel: {encoded}")
    if len(content.encode("utf-8")) > _TEXT_OPEN_MAX_BYTES:
        raise ValueError("File is too large to save from the panel.")
    fd, tmp_name = tempfile.mkstemp(dir=str(target.parent), prefix=f".{target.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as fh:
            fh.write(content)
        os.replace(tmp_name, target)
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise
    return {"path": _encode_ext_path(target)}


def open_project_file(relative_path: str) -> None:
    """Open a project or workspace file with the OS default application."""
    target = _resolve_file_path(relative_path)
    if not _is_abs_encoded_path(relative_path):
        _require_under_content(target, relative_path)
    if not target.is_file():
        raise ValueError(f"Not a file: {relative_path}")
    os.startfile(str(target))  # type: ignore[attr-defined]


def create_project_folder(parent_relative: str, name: str) -> dict[str, str]:
    """Create a folder under Content."""
    _require_writable_content_path(parent_relative)
    rel = _join_content_path(parent_relative, name)
    target = _resolve_relative(rel)
    _require_under_content(target, rel)
    if target.exists():
        raise ValueError(f"Already exists: {rel}")
    try:
        target.mkdir(parents=True, exist_ok=False)
    except OSError as exc:
        raise ValueError(str(exc)) from exc
    _invalidate_file_paths_cache()
    root = _project_root().resolve()
    return {"path": str(target.relative_to(root)).replace("\\", "/")}


def _is_descendant_or_self(ancestor: Path, candidate: Path) -> bool:
    return _is_path_under(candidate, ancestor)


def move_project_entry(source_relative: str, dest_parent_relative: str) -> dict[str, str]:
    """Move a file or folder under Content into another Content folder."""
    _require_writable_mutation(source_relative)
    _require_writable_content_path(dest_parent_relative)
    source_rel = source_relative.strip().replace("\\", "/").strip("/")
    if source_rel == CONTENT_DIR:
        raise ValueError("Cannot move Content root.")
    source = _resolve_relative(source_rel)
    _require_under_content(source, source_rel)
    if not source.exists():
        raise ValueError(f"Not found: {source_relative}")

    parent_rel = (dest_parent_relative or CONTENT_DIR).strip().replace("\\", "/").strip("/")
    if not parent_rel:
        parent_rel = CONTENT_DIR
    dest_parent = _resolve_relative(parent_rel)
    _require_under_content(dest_parent, parent_rel)
    if not dest_parent.is_dir():
        raise ValueError(f"Not a directory: {dest_parent_relative}")

    if source.is_dir() and _is_descendant_or_self(source, dest_parent):
        raise ValueError("Cannot move a folder into itself or a descendant.")

    dest = dest_parent / source.name
    if dest.resolve() == source.resolve():
        root = _project_root().resolve()
        return {"path": str(source.relative_to(root)).replace("\\", "/")}
    if dest.exists():
        raise ValueError(f"Already exists at destination: {source.name}")

    try:
        shutil.move(str(source), str(dest))
    except OSError as exc:
        raise ValueError(str(exc)) from exc
    _invalidate_file_paths_cache()

    root = _project_root().resolve()
    return {"path": str(dest.relative_to(root)).replace("\\", "/")}


def _dedupe_dest_name(dest_parent: Path, name: str, *, is_dir: bool) -> str:
    """VS Code-style collision rename: 'name copy', 'name copy 2', … (extension preserved for files)."""
    if not (dest_parent / name).exists():
        return name
    stem, suffix = (name, "") if is_dir else (Path(name).stem, Path(name).suffix)
    candidate = f"{stem} copy{suffix}"
    n = 1
    while (dest_parent / candidate).exists():
        n += 1
        candidate = f"{stem} copy {n}{suffix}"
    return candidate


def copy_project_entry(source_relative: str, dest_parent_relative: str) -> dict[str, str]:
    """Copy a file or folder under Content into another Content folder (auto-renames on collision)."""
    _require_writable_mutation(source_relative)
    _require_writable_content_path(dest_parent_relative)
    source_rel = source_relative.strip().replace("\\", "/").strip("/")
    if source_rel == CONTENT_DIR:
        raise ValueError("Cannot copy Content root.")
    source = _resolve_relative(source_rel)
    _require_under_content(source, source_rel)
    if not source.exists():
        raise ValueError(f"Not found: {source_relative}")

    parent_rel = (dest_parent_relative or CONTENT_DIR).strip().replace("\\", "/").strip("/") or CONTENT_DIR
    dest_parent = _resolve_relative(parent_rel)
    _require_under_content(dest_parent, parent_rel)
    if not dest_parent.is_dir():
        raise ValueError(f"Not a directory: {dest_parent_relative}")

    is_dir = source.is_dir()
    if is_dir and _is_descendant_or_self(source, dest_parent):
        raise ValueError("Cannot copy a folder into itself or a descendant.")

    dest = dest_parent / _dedupe_dest_name(dest_parent, source.name, is_dir=is_dir)
    try:
        if is_dir:
            shutil.copytree(source, dest)
        else:
            shutil.copy2(source, dest)
    except OSError as exc:
        raise ValueError(str(exc)) from exc
    _invalidate_file_paths_cache()
    root = _project_root().resolve()
    return {"path": str(dest.relative_to(root)).replace("\\", "/")}


def import_external_entries(
    dest_parent_relative: str, source_paths: list[str]
) -> dict[str, object]:
    """Copy files/folders from anywhere on disk into a Content folder.

    Drives the "drag a file from Explorer into the Content tree" feature: the real
    source paths come from pywebview's OS-drop handler (page JS can't see them).
    Files use copy2, folders copy recursively; collisions get a VS Code-style
    "name copy" rename. Bad sources are skipped and reported in "errors" rather than
    failing the whole drop.
    """
    _require_writable_content_path(dest_parent_relative)
    parent_rel = (dest_parent_relative or CONTENT_DIR).strip().replace("\\", "/").strip("/") or CONTENT_DIR
    dest_parent = _resolve_relative(parent_rel)
    _require_under_content(dest_parent, parent_rel)
    if not dest_parent.is_dir():
        raise ValueError(f"Not a directory: {dest_parent_relative}")

    root = _project_root().resolve()
    created: list[str] = []
    errors: list[str] = []
    for raw in source_paths or []:
        src_text = (raw or "").strip()
        if not src_text:
            continue
        try:
            source = Path(src_text).resolve()
        except OSError:
            errors.append(f"{src_text}: cannot resolve path")
            continue
        if not source.name:
            errors.append(f"{src_text}: not a file or folder")
            continue
        if not source.exists():
            errors.append(f"{source.name}: not found")
            continue
        is_dir = source.is_dir()
        # Guard the pathological case of dropping a folder onto itself/an ancestor.
        if is_dir and _is_descendant_or_self(source, dest_parent):
            errors.append(f"{source.name}: cannot copy a folder into itself")
            continue
        dest = dest_parent / _dedupe_dest_name(dest_parent, source.name, is_dir=is_dir)
        try:
            if is_dir:
                shutil.copytree(source, dest)
            else:
                shutil.copy2(source, dest)
        except OSError as exc:
            errors.append(f"{source.name}: {exc}")
            continue
        created.append(str(dest.relative_to(root)).replace("\\", "/"))
    if created:
        _invalidate_file_paths_cache()
    return {"paths": created, "errors": errors}


# Soft-delete "trash" so Ctrl+Z can bring a deleted file/folder back (VS Code style).
# Kept as a hidden sibling of Content (same volume → deletes stay instant renames, and a
# dot-prefixed root dir is skipped by both our tree walk and UEFN). The token→metadata map
# is in-memory: undo is a within-session action, so a restart legitimately forgets it (and
# purge_undo_trash clears the now-unrestorable files on startup).
TRASH_DIR_NAME = ".undo-trash"
_trash_registry: dict[str, dict[str, object]] = {}


def _trash_dir() -> Path:
    d = _project_root().resolve() / TRASH_DIR_NAME
    d.mkdir(parents=True, exist_ok=True)
    return d


def purge_undo_trash() -> None:
    """Drop all trashed entries — call on startup (the in-memory registry is already empty,
    so anything left on disk can never be restored)."""
    _trash_registry.clear()
    try:
        root = _project_root().resolve()
    except (ValueError, OSError):
        return
    trash = root / TRASH_DIR_NAME
    if trash.is_dir():
        shutil.rmtree(trash, ignore_errors=True)


def delete_project_entry(relative_path: str) -> dict[str, str]:
    """Move a file or folder under Content to the undo-trash (restorable via Ctrl+Z)."""
    _require_writable_mutation(relative_path)
    rel = relative_path.strip().replace("\\", "/").strip("/")
    if not rel or rel == CONTENT_DIR:
        raise ValueError("Cannot delete Content root.")
    target = _resolve_relative(rel)
    _require_under_content(target, rel)
    if not target.exists():
        raise ValueError(f"Not found: {relative_path}")
    token = uuid.uuid4().hex
    slot = _trash_dir() / token
    slot.mkdir(parents=True, exist_ok=True)
    dest = slot / target.name
    try:
        shutil.move(str(target), str(dest))
    except OSError as exc:
        raise ValueError(str(exc)) from exc
    _trash_registry[token] = {
        "orig": str(target),
        "trash": str(dest),
        "rel": rel,
        "name": target.name,
        "ts": time.time(),
    }
    _invalidate_file_paths_cache()
    return {"path": rel, "trash_token": token, "name": target.name}


def restore_trashed_entry(trash_token: str) -> dict[str, str]:
    """Move a trashed entry back to where it was deleted from (Ctrl+Z undo)."""
    meta = _trash_registry.get(trash_token)
    if not meta:
        raise ValueError("Nothing to restore — already restored or expired.")
    trash_path = Path(str(meta["trash"]))
    orig = Path(str(meta["orig"]))
    if not trash_path.exists():
        _trash_registry.pop(trash_token, None)
        raise ValueError("Trashed item is no longer available.")
    orig.parent.mkdir(parents=True, exist_ok=True)
    dest = orig
    if dest.exists():
        dest = orig.parent / _dedupe_dest_name(orig.parent, orig.name, is_dir=trash_path.is_dir())
    try:
        shutil.move(str(trash_path), str(dest))
    except OSError as exc:
        raise ValueError(str(exc)) from exc
    try:
        trash_path.parent.rmdir()  # remove the now-empty token slot
    except OSError:
        pass
    _trash_registry.pop(trash_token, None)
    _invalidate_file_paths_cache()
    root = _project_root().resolve()
    try:
        rel = str(dest.relative_to(root)).replace("\\", "/")
    except ValueError:
        rel = str(meta.get("rel") or dest.name)
    return {"path": rel}


def rename_project_entry(source_relative: str, new_name: str) -> dict[str, str]:
    """Rename a file or folder in place under Content."""
    _require_writable_mutation(source_relative)
    safe_name = _validate_entry_name(new_name)
    _require_not_digest(safe_name)
    source_rel = source_relative.strip().replace("\\", "/").strip("/")
    if not source_rel or source_rel == CONTENT_DIR:
        raise ValueError("Cannot rename Content root.")
    source = _resolve_relative(source_rel)
    _require_under_content(source, source_rel)
    if not source.exists():
        raise ValueError(f"Not found: {source_relative}")
    dest = source.parent / safe_name
    if dest.resolve() == source.resolve():
        root = _project_root().resolve()
        return {"path": str(source.relative_to(root)).replace("\\", "/")}
    if dest.exists():
        raise ValueError(f"Already exists: {safe_name}")
    try:
        source.rename(dest)
    except OSError as exc:
        raise ValueError(str(exc)) from exc
    _invalidate_file_paths_cache()
    root = _project_root().resolve()
    return {"path": str(dest.relative_to(root)).replace("\\", "/")}


def create_project_verse_file(parent_relative: str, name: str, content: str = "") -> dict[str, str]:
    """Create a new .verse file under Content."""
    _require_writable_content_path(parent_relative)
    safe_name = _validate_entry_name(name)
    if not safe_name.lower().endswith(".verse"):
        safe_name = f"{safe_name}.verse"
    _require_not_digest(safe_name)
    rel = _join_content_path(parent_relative, safe_name)
    target = _resolve_relative(rel)
    _require_under_content(target, rel)
    if target.exists():
        raise ValueError(f"Already exists: {rel}")
    text = content if content else _DEFAULT_VERSE_FILE
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        target.write_text(text, encoding="utf-8", newline="\n")
    except OSError as exc:
        raise ValueError(str(exc)) from exc
    _invalidate_file_paths_cache()
    root = _project_root().resolve()
    return {"path": str(target.relative_to(root)).replace("\\", "/")}


def create_project_file(parent_relative: str, name: str, content: str = "") -> dict[str, str]:
    """Create a new text file under Content (any editable extension)."""
    _require_writable_content_path(parent_relative)
    safe_name = _validate_entry_name(name)
    if not Path(safe_name).suffix:
        safe_name = f"{safe_name}.txt"
    if _is_binary_file_name(safe_name):
        raise ValueError(f"Cannot create binary file: {safe_name}")
    rel = _join_content_path(parent_relative, safe_name)
    if not is_editable_text_file(rel):
        raise ValueError(f"Unsupported file type: {safe_name}")
    target = _resolve_relative(rel)
    _require_under_content(target, rel)
    if target.exists():
        raise ValueError(f"Already exists: {rel}")
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        target.write_text(content, encoding="utf-8", newline="\n")
    except OSError as exc:
        raise ValueError(str(exc)) from exc
    _invalidate_file_paths_cache()
    root = _project_root().resolve()
    return {"path": str(target.relative_to(root)).replace("\\", "/")}
