"""Bundled and custom ducky avatar assets."""

from __future__ import annotations

import base64
import json
import re
from dataclasses import dataclass
from pathlib import Path

from frontend.app_paths import resolve_app_data_dir

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
CUSTOM_PREFIX = "custom:"
_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
_BUNDLED_JSON = Path(__file__).resolve().parent / "bundled_duckies.json"
_PUBLIC_DUCKIES = (
    Path(__file__).resolve().parent / "ui_web" / "web" / "public" / "duckies"
)
_DIST_DUCKIES = Path(__file__).resolve().parent / "ui_web" / "web" / "dist" / "duckies"


@dataclass(frozen=True)
class DuckyEntry:
    id: str
    label: str
    file: str
    kind: str  # "builtin" | "custom"

    def to_dict(self) -> dict[str, str]:
        return {
            "id": self.id,
            "label": self.label,
            "file": self.file,
            "kind": self.kind,
        }


def custom_duckies_dir(*, for_write: bool = False) -> Path:
    path = resolve_app_data_dir(for_write=for_write) / "duckies"
    if for_write:
        path.mkdir(parents=True, exist_ok=True)
    return path


def _label_from_slug(slug: str) -> str:
    parts = [p for p in re.split(r"[-_]+", slug) if p]
    return " ".join(p[:1].upper() + p[1:] for p in parts) if parts else "Ducky"


def _slug_from_filename(name: str) -> str:
    stem = Path(name).stem
    return stem.lower()


def _scan_duckies_folder(folder: Path) -> list[DuckyEntry]:
    if not folder.is_dir():
        return []
    files = sorted(
        (p for p in folder.glob("*.png") if p.is_file()),
        key=lambda p: _slug_from_filename(p.name),
    )
    return [
        DuckyEntry(
            id=_slug_from_filename(p.name),
            label=_label_from_slug(_slug_from_filename(p.name)),
            file=p.name,
            kind="builtin",
        )
        for p in files
    ]


def _scan_public_duckies() -> list[DuckyEntry]:
    return _scan_duckies_folder(_PUBLIC_DUCKIES)


def _scan_dist_duckies() -> list[DuckyEntry]:
    return _scan_duckies_folder(_DIST_DUCKIES)


def list_bundled_duckies() -> list[DuckyEntry]:
    if _BUNDLED_JSON.is_file():
        try:
            data = json.loads(_BUNDLED_JSON.read_text(encoding="utf-8"))
            duckies = data.get("duckies") or []
            out: list[DuckyEntry] = []
            for item in duckies:
                if not isinstance(item, dict):
                    continue
                duck_id = str(item.get("id", "")).strip()
                file_name = str(item.get("file", "")).strip()
                if not duck_id or not file_name:
                    continue
                out.append(
                    DuckyEntry(
                        id=duck_id,
                        label=str(item.get("label", "")).strip() or _label_from_slug(duck_id),
                        file=file_name,
                        kind="builtin",
                    )
                )
            if out:
                return out
        except (OSError, json.JSONDecodeError, TypeError):
            pass
    for scanner in (_scan_dist_duckies, _scan_public_duckies):
        entries = scanner()
        if entries:
            return entries
    return []


def default_bundled_style() -> str:
    if _BUNDLED_JSON.is_file():
        try:
            data = json.loads(_BUNDLED_JSON.read_text(encoding="utf-8"))
            raw = str(data.get("default_style", "")).strip()
            if raw:
                return raw
        except (OSError, json.JSONDecodeError, TypeError):
            pass
    bundled = list_bundled_duckies()
    return bundled[0].id if bundled else "classic"


def _bundled_ids() -> frozenset[str]:
    return frozenset(d.id for d in list_bundled_duckies())


def list_custom_duckies() -> list[DuckyEntry]:
    folder = custom_duckies_dir()
    if not folder.is_dir():
        return []
    files = sorted(
        (p for p in folder.glob("*.png") if p.is_file()),
        key=lambda p: _slug_from_filename(p.name),
    )
    return [
        DuckyEntry(
            id=f"{CUSTOM_PREFIX}{_slug_from_filename(p.name)}",
            label=_label_from_slug(_slug_from_filename(p.name)),
            file=p.name,
            kind="custom",
        )
        for p in files
    ]


def list_ducky_catalog() -> dict[str, object]:
    builtin = list_bundled_duckies()
    custom = list_custom_duckies()
    return {
        "builtin": [d.to_dict() for d in builtin],
        "custom": [d.to_dict() for d in custom],
        "default_style": default_bundled_style(),
    }


def _custom_slug_from_style(style_id: str) -> str | None:
    raw = (style_id or "").strip()
    if not raw.startswith(CUSTOM_PREFIX):
        return None
    slug = raw[len(CUSTOM_PREFIX) :].strip().lower()
    return slug if _SLUG_RE.match(slug) else None


def custom_ducky_path(slug: str) -> Path:
    safe = slug.strip().lower()
    if not _SLUG_RE.match(safe):
        raise ValueError("Invalid custom ducky id")
    return custom_duckies_dir() / f"{safe}.png"


def custom_ducky_exists(slug: str) -> bool:
    try:
        return custom_ducky_path(slug).is_file()
    except ValueError:
        return False


def normalize_ducky_style(style: str | None) -> str:
    raw = (style or "").strip()
    if not raw:
        return default_bundled_style()

    slug = _custom_slug_from_style(raw)
    if slug is not None:
        return raw if custom_ducky_exists(slug) else default_bundled_style()

    lowered = raw.lower()
    if lowered in _bundled_ids():
        return lowered

    return default_bundled_style()


def ducky_style_label(style_id: str | None) -> str:
    """Human label for a style id (Artist, Hacker, …)."""
    style = normalize_ducky_style(style_id)
    for entry in list_bundled_duckies():
        if entry.id == style:
            return entry.label
    for entry in list_custom_duckies():
        if entry.id == style:
            return entry.label
    slug = _custom_slug_from_style(style)
    if slug:
        return _label_from_slug(slug)
    return _label_from_slug(style)


def normalize_upload_filename(name: str) -> str:
    cleaned = name.strip().replace("\\", "/").split("/")[-1]
    if not cleaned.lower().endswith(".png"):
        cleaned = f"{Path(cleaned).stem}.png"
    slug = _slug_from_filename(cleaned)
    if not _SLUG_RE.match(slug):
        raise ValueError("PNG name must be letters, numbers, _ or -")
    if slug in _bundled_ids():
        raise ValueError(f"{slug} is a built-in ducky — pick another name")
    return f"{slug}.png"


def _decode_png_bytes(png_base64: str) -> bytes:
    raw = (png_base64 or "").strip()
    if "," in raw and raw.lower().startswith("data:"):
        raw = raw.split(",", 1)[1]
    try:
        data = base64.b64decode(raw, validate=True)
    except (ValueError, TypeError) as exc:
        raise ValueError("Invalid base64 image data") from exc
    if len(data) < 8 or not data.startswith(PNG_MAGIC):
        raise ValueError("Only PNG images are supported")
    return data


def save_custom_ducky_png(filename: str, png_base64: str) -> DuckyEntry:
    safe_name = normalize_upload_filename(filename)
    slug = _slug_from_filename(safe_name)
    data = _decode_png_bytes(png_base64)
    dest = custom_duckies_dir(for_write=True) / safe_name
    dest.write_bytes(data)
    return DuckyEntry(
        id=f"{CUSTOM_PREFIX}{slug}",
        label=_label_from_slug(slug),
        file=safe_name,
        kind="custom",
    )


def delete_custom_ducky(style_id: str) -> bool:
    slug = _custom_slug_from_style(style_id)
    if not slug:
        return False
    path = custom_ducky_path(slug)
    if not path.is_file():
        return False
    path.unlink()
    return True
