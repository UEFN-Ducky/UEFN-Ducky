"""Custom Verse file / system templates stored in app data (not Store plugins)."""

from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from frontend.app_paths import resolve_app_data_dir

CUSTOM_PREFIX = "custom:"
_ID_RE = re.compile(r"^custom:[a-z0-9]{8,32}$")
_MAX_FILE_CHARS = 200 * 1024
_MAX_FILES = 40
_PATH_BAD = re.compile(r"(^|/|\\)\.\.(/|\\|$)")


@dataclass(frozen=True)
class VerseTemplateFileEntry:
    path: str
    content: str

    def to_dict(self) -> dict[str, str]:
        return {"path": self.path, "content": self.content}


@dataclass(frozen=True)
class VerseTemplateEntry:
    id: str
    name: str
    icon: str
    content: str
    kind: str  # "custom"
    folder: str = ""
    files: tuple[VerseTemplateFileEntry, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "id": self.id,
            "name": self.name,
            "icon": self.icon,
            "content": self.content,
            "kind": self.kind,
        }
        if self.folder:
            out["folder"] = self.folder
        if self.files:
            out["files"] = [f.to_dict() for f in self.files]
        return out


def custom_verse_templates_dir(*, for_write: bool = False) -> Path:
    path = resolve_app_data_dir(for_write=for_write) / "verse_templates"
    if for_write:
        path.mkdir(parents=True, exist_ok=True)
    return path


def _validate_name(name: str) -> str:
    cleaned = (name or "").strip()
    if not cleaned:
        raise ValueError("Template name is required")
    if len(cleaned) > 64:
        raise ValueError("Template name is too long")
    return cleaned


def _validate_icon(icon: str) -> str:
    cleaned = (icon or "").strip()
    if not cleaned:
        raise ValueError("Template icon is required")
    return cleaned[:16]


def _validate_folder(folder: str) -> str:
    """Pack root folder name — one segment (same as plugin verse.templates)."""
    cleaned = (folder or "").strip().replace("\\", "/")
    if not cleaned:
        return ""
    if (
        "/" in cleaned
        or ":" in cleaned
        or cleaned in (".", "..")
        or cleaned.startswith(".")
        or _PATH_BAD.search(cleaned)
    ):
        raise ValueError(f"Invalid folder name: {folder!r}")
    return cleaned[:80]


def _validate_rel_path(path: str) -> str:
    cleaned = (path or "").strip().replace("\\", "/")
    if not cleaned or cleaned.startswith("/") or _PATH_BAD.search(cleaned):
        raise ValueError(f"Invalid file path: {path!r}")
    parts = [p for p in cleaned.split("/") if p]
    if not parts or any(p in (".", "..") for p in parts):
        raise ValueError(f"Invalid file path: {path!r}")
    return "/".join(parts)


def _normalize_files(raw_files: Any) -> tuple[VerseTemplateFileEntry, ...]:
    if raw_files is None:
        return ()
    if not isinstance(raw_files, list):
        raise ValueError("files must be a list of {path, content}")
    if len(raw_files) > _MAX_FILES:
        raise ValueError(f"at most {_MAX_FILES} files per template")
    out: list[VerseTemplateFileEntry] = []
    seen: set[str] = set()
    for item in raw_files:
        if not isinstance(item, dict):
            raise ValueError("each file must be an object with path and content")
        path = _validate_rel_path(str(item.get("path") or ""))
        if path in seen:
            raise ValueError(f"duplicate file path: {path}")
        seen.add(path)
        content = item.get("content")
        text = "" if content is None else str(content)
        if len(text) > _MAX_FILE_CHARS:
            raise ValueError(f"file {path} exceeds {_MAX_FILE_CHARS} chars")
        out.append(VerseTemplateFileEntry(path=path, content=text))
    return tuple(out)


def _custom_id_from_style(template_id: str) -> str | None:
    raw = (template_id or "").strip()
    if not raw.startswith(CUSTOM_PREFIX):
        return None
    slug = raw[len(CUSTOM_PREFIX) :].strip().lower()
    return slug if _ID_RE.match(raw) else None


def _preview_content(content: str, files: tuple[VerseTemplateFileEntry, ...]) -> str:
    text = content if content is not None else ""
    if text.strip():
        return text
    if files:
        return files[0].content
    return ""


def _read_entry(path: Path) -> VerseTemplateEntry | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return None
        entry_id = str(data.get("id", "")).strip()
        name = str(data.get("name", "")).strip()
        icon = str(data.get("icon", "")).strip()
        content = str(data.get("content", ""))
        if not entry_id or not name or not icon:
            return None
        folder = str(data.get("folder") or "").strip()
        try:
            files = _normalize_files(data.get("files"))
        except ValueError:
            files = ()
        return VerseTemplateEntry(
            id=entry_id,
            name=name,
            icon=icon,
            content=content,
            kind="custom",
            folder=folder,
            files=files,
        )
    except (OSError, json.JSONDecodeError, TypeError):
        return None


def list_custom_verse_templates() -> list[VerseTemplateEntry]:
    folder = custom_verse_templates_dir()
    if not folder.is_dir():
        return []
    entries: list[VerseTemplateEntry] = []
    for path in folder.glob("*.json"):
        entry = _read_entry(path)
        if entry is not None:
            entries.append(entry)
    return sorted(entries, key=lambda e: e.name.lower())


def get_custom_verse_template(template_id: str) -> VerseTemplateEntry | None:
    slug = _custom_id_from_style(template_id)
    if not slug:
        return None
    path = custom_verse_templates_dir() / f"{slug}.json"
    if not path.is_file():
        return None
    return _read_entry(path)


def save_custom_verse_template(
    name: str,
    icon: str,
    content: str = "",
    *,
    template_id: str = "",
    folder: str = "",
    files: Any = None,
) -> VerseTemplateEntry:
    """Create or update a user/AI Verse template. Never touches Store plugins."""
    safe_name = _validate_name(name)
    safe_icon = _validate_icon(icon)
    safe_folder = _validate_folder(folder)
    file_entries = _normalize_files(files)
    if file_entries and not safe_folder:
        # Default pack folder from name (plugin-style).
        slug_name = re.sub(r"[^a-zA-Z0-9_-]+", "", safe_name.replace(" ", "")) or "Pack"
        safe_folder = slug_name[:80]
    if safe_folder and not file_entries:
        raise ValueError("multi-file templates need at least one file in files[]")
    safe_content = _preview_content(content if content is not None else "", file_entries)

    tid = (template_id or "").strip()
    if tid:
        slug = _custom_id_from_style(tid)
        if not slug:
            raise ValueError("Invalid custom template id — Store/plugin templates cannot be edited")
        existing = get_custom_verse_template(tid)
        if existing is None:
            raise FileNotFoundError(f"Custom template not found: {tid}")
        entry_id = tid
    else:
        entry_id = f"{CUSTOM_PREFIX}{uuid.uuid4().hex[:12]}"
        slug = entry_id[len(CUSTOM_PREFIX) :]

    entry = VerseTemplateEntry(
        id=entry_id,
        name=safe_name,
        icon=safe_icon,
        content=safe_content,
        kind="custom",
        folder=safe_folder,
        files=file_entries,
    )
    dest = custom_verse_templates_dir(for_write=True) / f"{slug}.json"
    dest.write_text(
        json.dumps(entry.to_dict(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return entry


def delete_custom_verse_template(template_id: str) -> bool:
    slug = _custom_id_from_style(template_id)
    if not slug:
        return False
    path = custom_verse_templates_dir() / f"{slug}.json"
    if not path.is_file():
        return False
    path.unlink()
    return True


def apply_custom_verse_template(
    template_id: str,
    *,
    parent_relative: str = "Content/Verse",
) -> dict[str, Any]:
    """Materialize a custom template into the open project (single file or pack).

    Mirrors Store ``verse_template_apply`` / UI ``createVerseTemplatePack``.
    """
    entry = get_custom_verse_template(template_id)
    if entry is None:
        return {"ok": False, "error": f"Custom template not found: {template_id}"}
    from frontend.ui_web.project_files import create_project_folder, create_project_verse_file

    parent = (parent_relative or "Content/Verse").strip().replace("\\", "/").strip("/")
    if not parent:
        parent = "Content/Verse"
    created: list[str] = []
    try:
        pack_root = parent
        folder = (entry.folder or "").strip().replace("\\", "/").strip("/")
        pack_files: list[dict[str, str]]
        if entry.files:
            if folder and ".." not in folder.split("/"):
                for n in range(0, 50):
                    candidate = folder if n == 0 else f"{folder}{n + 1}"
                    try:
                        result = create_project_folder(parent, candidate)
                        pack_root = result["path"]
                        created.append(pack_root)
                        break
                    except ValueError as exc:
                        if "Already exists" not in str(exc):
                            raise
                else:
                    return {"ok": False, "error": f"could not create folder {folder}"}
            pack_files = [{"path": f.path, "content": f.content} for f in entry.files]
        else:
            base_name = re.sub(r"[^a-zA-Z0-9_]+", "_", entry.name).strip("_") or "NewDevice"
            pack_files = [{"path": f"{base_name}.verse", "content": entry.content}]

        made_dirs: set[str] = {pack_root}
        for item in pack_files:
            rel = item["path"].replace("\\", "/").lstrip("/")
            if not rel or ".." in rel.split("/"):
                continue
            parts = [p for p in rel.split("/") if p]
            if not parts:
                continue
            file_name = parts[-1]
            dir_path = pack_root
            for seg in parts[:-1]:
                nxt = f"{dir_path}/{seg}"
                if nxt not in made_dirs:
                    try:
                        made = create_project_folder(dir_path, seg)
                        dir_path = made["path"]
                        made_dirs.add(dir_path)
                        created.append(dir_path)
                    except ValueError:
                        dir_path = nxt
                        made_dirs.add(nxt)
                else:
                    dir_path = nxt
            for n in range(0, 50):
                candidate = file_name if n == 0 else _numbered_verse_name(file_name, n + 1)
                try:
                    written = create_project_verse_file(dir_path, candidate, item["content"])
                    created.append(written["path"])
                    break
                except ValueError as exc:
                    if "Already exists" not in str(exc):
                        raise
            else:
                return {"ok": False, "error": f"could not create file {file_name}", "created": created}
    except Exception as exc:
        return {"ok": False, "error": str(exc), "created": created}
    kind = "pack" if entry.files else "file"
    return {"ok": True, "id": entry.id, "folder": pack_root if entry.files else "", "files": created, "kind": kind}


def _numbered_verse_name(file_name: str, n: int) -> str:
    if file_name.lower().endswith(".verse"):
        stem = file_name[:-6]
        return f"{stem}_{n}.verse"
    return f"{file_name}_{n}"
