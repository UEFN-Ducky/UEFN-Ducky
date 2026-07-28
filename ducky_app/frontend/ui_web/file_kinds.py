"""Shared project-file kind classification (text / image / unreal_asset / binary).

Used by project file reads, write gates, and the panel media server so the UI
routes files like VS Code instead of dumping hex or handing off to Windows.
"""

from __future__ import annotations

import mimetypes
from pathlib import Path
from typing import Literal
from urllib.parse import quote

FileKind = Literal["text", "image", "model", "audio", "video", "unreal_asset", "binary"]

# Browser-decodable image formats rendered in ImageFilePane.
IMAGE_SUFFIXES = frozenset(
    {
        ".png",
        ".jpg",
        ".jpeg",
        ".gif",
        ".bmp",
        ".webp",
        ".ico",
        ".svg",
    }
)

# 3D meshes previewed in ModelFilePane (Three.js loaders).
MODEL_SUFFIXES = frozenset(
    {
        ".fbx",
        ".glb",
        ".gltf",
        ".obj",
        ".stl",
        ".ply",
        ".dae",
    }
)

# Browser-decodable audio formats rendered in AudioFilePane (<audio controls>).
AUDIO_SUFFIXES = frozenset(
    {
        ".mp3",
        ".wav",
        ".ogg",
        ".m4a",
        ".aac",
        ".flac",
    }
)

# Browser-decodable video formats rendered in VideoFilePane (<video controls>).
VIDEO_SUFFIXES = frozenset(
    {
        ".mp4",
        ".webm",
        ".mov",
        ".m4v",
        ".ogv",
    }
)

# Image-like / design formats we treat as binary (no in-panel raster preview).
UNSUPPORTED_IMAGE_SUFFIXES = frozenset(
    {
        ".tga",
        ".psd",
        ".tif",
        ".tiff",
        ".exr",
        ".hdr",
    }
)

UNREAL_ASSET_SUFFIXES = frozenset(
    {
        ".umap",
        ".uasset",
        ".ubulk",
        ".uexp",
        ".uptod",
        ".udic",
        ".ufont",
        ".ushaderbytecode",
        ".upipelinecache",
    }
)

BINARY_NON_IMAGE_SUFFIXES = frozenset(
    {
        ".exe",
        ".dll",
        ".pdb",
        ".zip",
        ".7z",
        ".rar",
        ".pak",
        ".bin",
        ".dat",
        ".ttf",
        ".otf",
        ".woff",
        ".woff2",
    }
) | UNSUPPORTED_IMAGE_SUFFIXES

# Union used by legacy callers that still ask "is this a binary suffix?"
BINARY_FILE_SUFFIXES = (
    UNREAL_ASSET_SUFFIXES
    | IMAGE_SUFFIXES
    | MODEL_SUFFIXES
    | AUDIO_SUFFIXES
    | VIDEO_SUFFIXES
    | BINARY_NON_IMAGE_SUFFIXES
)

EDITABLE_TEXT_SUFFIXES = frozenset(
    {
        ".verse",
        ".versetest",
        ".vson",
        ".py",
        ".txt",
        ".json",
        ".md",
        ".cfg",
        ".ini",
        ".toml",
        ".yaml",
        ".yml",
        ".xml",
        ".csv",
        ".log",
        ".ts",
        ".tsx",
        ".js",
        ".jsx",
        ".mjs",
        ".cjs",
        ".css",
        ".scss",
        ".less",
        ".html",
        ".htm",
        ".rs",
        ".php",
        ".c",
        ".h",
        ".cpp",
        ".cxx",
        ".cc",
        ".hpp",
        ".hh",
        ".go",
        ".gitignore",
        ".gitattributes",
        ".editorconfig",
        ".dockerignore",
        ".env",
        ".htaccess",
        ".npmrc",
        ".prettierrc",
        ".eslintrc",
    }
)

# Basenames without (or with) extension that are always treated as text.
KNOWN_TEXT_FILENAMES = frozenset(
    {
        ".gitignore",
        ".gitattributes",
        ".editorconfig",
        ".dockerignore",
        ".env",
        ".env.local",
        ".env.development",
        ".env.production",
        ".npmrc",
        ".prettierrc",
        ".eslintrc",
        ".htaccess",
        "dockerfile",
        "makefile",
        "gnumakefile",
        "cmakelists.txt",
        "license",
        "licence",
        "copying",
        "readme",
        "readme.md",
        "readme.txt",
        "authors",
        "contributors",
        "changelog",
        "changes",
        "todo",
        "gemfile",
        "rakefile",
        "procfile",
        "vagrantfile",
        "jenkinsfile",
        "pipfile",
        "poetry.lock",
        "package-lock.json",
        "yarn.lock",
        "pnpm-lock.yaml",
    }
)

# Dotfiles always shown in the project tree (even when "show hidden" is off).
VISIBLE_DOTFILE_NAMES = frozenset(
    {
        ".gitignore",
        ".gitattributes",
        ".editorconfig",
        ".dockerignore",
        ".env",
        ".uefnproject",
    }
)

# Dot-directories that stay hidden even when showing hidden files.
ALWAYS_SKIP_DOT_DIRS = frozenset({".git", ".svn", ".hg"})


def _basename(relative_path: str) -> str:
    norm = (relative_path or "").strip().replace("\\", "/")
    # Strip scheme prefixes used by the panel (abs:/ext:/ws:N/...).
    for prefix in ("abs:", "ext:"):
        if norm.lower().startswith(prefix):
            norm = norm[len(prefix) :]
            break
    if norm.lower().startswith("ws:"):
        # ws:0/foo/bar → foo/bar
        rest = norm[3:]
        slash = rest.find("/")
        norm = rest[slash + 1 :] if slash >= 0 else rest
    return Path(norm).name


def _suffix(name: str) -> str:
    return Path(name).suffix.lower()


def is_known_text_filename(name: str) -> bool:
    base = Path(name).name
    lower = base.lower()
    if lower in KNOWN_TEXT_FILENAMES:
        return True
    # Stem-only matches: README, LICENSE, Dockerfile, Makefile (any case).
    stem = Path(base).stem.lower() if Path(base).suffix else lower
    if stem in {"readme", "license", "licence", "copying", "authors", "changelog", "changes", "todo"}:
        return True
    if lower in {"dockerfile", "makefile", "gnumakefile", "gemfile", "rakefile", "procfile", "vagrantfile", "jenkinsfile", "pipfile"}:
        return True
    return False


def is_image_file_name(name: str) -> bool:
    return _suffix(name) in IMAGE_SUFFIXES


def is_model_file_name(name: str) -> bool:
    return _suffix(name) in MODEL_SUFFIXES


def is_audio_file_name(name: str) -> bool:
    return _suffix(name) in AUDIO_SUFFIXES


def is_video_file_name(name: str) -> bool:
    return _suffix(name) in VIDEO_SUFFIXES


def is_unreal_asset_name(name: str) -> bool:
    return _suffix(name) in UNREAL_ASSET_SUFFIXES


def is_binary_file_name(name: str) -> bool:
    return _suffix(name) in BINARY_FILE_SUFFIXES


def is_text_exempt_from_nul_sniff(name: str) -> bool:
    """Editable text suffixes / known names must never become hex-in-editor."""
    base = Path(name).name
    if is_known_text_filename(base):
        return True
    return _suffix(base) in EDITABLE_TEXT_SUFFIXES


def looks_like_text(head: bytes) -> bool:
    """Bounded UTF-8-ish sniff: reject NULs; allow common text."""
    if not head:
        return True
    if b"\x00" in head:
        return False
    # Reject high ratio of non-text control bytes (other than tab/CR/LF).
    sample = head[:4096]
    if not sample:
        return True
    weird = sum(1 for b in sample if b < 9 or (13 < b < 32 and b != 27))
    return (weird / len(sample)) < 0.30


def mime_for_path(relative_path: str) -> str:
    name = _basename(relative_path)
    guessed, _ = mimetypes.guess_type(name)
    if guessed:
        return guessed
    suffix = _suffix(name)
    if suffix == ".svg":
        return "image/svg+xml"
    if suffix == ".glb":
        return "model/gltf-binary"
    if suffix == ".gltf":
        return "model/gltf+json"
    if suffix == ".fbx":
        return "application/octet-stream"
    if suffix == ".obj":
        return "text/plain"
    if suffix == ".stl":
        return "model/stl"
    if suffix == ".ply":
        return "application/x-ply"
    if suffix == ".dae":
        return "model/vnd.collada+xml"
    if suffix in IMAGE_SUFFIXES:
        return "image/png"
    # mimetypes.guess_type resolves most of these, but not reliably on every
    # platform (e.g. .flac, .m4a, .ogv can come back empty) — explicit fallbacks.
    if suffix == ".mp3":
        return "audio/mpeg"
    if suffix == ".wav":
        return "audio/wav"
    if suffix == ".ogg":
        return "audio/ogg"
    if suffix == ".m4a":
        return "audio/mp4"
    if suffix == ".aac":
        return "audio/aac"
    if suffix == ".flac":
        return "audio/flac"
    if suffix == ".mp4":
        return "video/mp4"
    if suffix == ".webm":
        return "video/webm"
    if suffix == ".mov":
        return "video/quicktime"
    if suffix == ".m4v":
        return "video/x-m4v"
    if suffix == ".ogv":
        return "video/ogg"
    if is_known_text_filename(name) or suffix in EDITABLE_TEXT_SUFFIXES:
        return "text/plain"
    return "application/octet-stream"


def classify_by_name(relative_path: str) -> FileKind | None:
    """Deterministic kind from basename/suffix only. None = needs content sniff."""
    name = _basename(relative_path)
    suffix = _suffix(name)
    if suffix in UNREAL_ASSET_SUFFIXES:
        return "unreal_asset"
    if suffix in IMAGE_SUFFIXES:
        return "image"
    if suffix in MODEL_SUFFIXES:
        return "model"
    if suffix in AUDIO_SUFFIXES:
        return "audio"
    if suffix in VIDEO_SUFFIXES:
        return "video"
    if suffix in BINARY_NON_IMAGE_SUFFIXES:
        return "binary"
    if is_known_text_filename(name) or suffix in EDITABLE_TEXT_SUFFIXES:
        return "text"
    return None


def classify_project_file(relative_path: str, *, head: bytes | None = None) -> dict[str, object]:
    """Return kind/mime/editable metadata for a project or external path."""
    name = _basename(relative_path)
    by_name = classify_by_name(relative_path)
    kind: FileKind
    if by_name is not None:
        kind = by_name
    elif head is not None:
        kind = "text" if looks_like_text(head) else "binary"
    else:
        # Extensionless / unknown without bytes — prefer text so Monaco can try;
        # read_project_file will reclassify with a sniff.
        kind = "text"

    mime = mime_for_path(relative_path)
    editable = kind == "text"
    return {
        "kind": kind,
        "mime": mime,
        "editable": editable,
        "name": name,
    }


def should_show_dot_entry(name: str, is_dir: bool, *, show_hidden: bool) -> bool:
    """Whether a dotfile/dotdir should appear in the tree."""
    if not name.startswith("."):
        return True
    if is_dir:
        if name in ALWAYS_SKIP_DOT_DIRS:
            return False
        return show_hidden
    if name in VISIBLE_DOTFILE_NAMES or is_known_text_filename(name):
        return True
    return show_hidden


def media_path_token(encoded_path: str) -> str:
    """URL-safe token encoding a panel path for /project-media/<token>."""
    return quote(encoded_path, safe="")
