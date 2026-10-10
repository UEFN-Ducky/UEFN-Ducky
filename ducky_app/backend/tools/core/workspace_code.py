"""Read, search, edit, move and inspect git in the project without a shell.

Coding agents (Codex above all) otherwise spawn PowerShell for every lookup:
``git ls-files``, ``Get-Content``, ``Select-String``, a Python one-liner. Each is
a separate step in the chat, slow to start on Windows, easy to quote wrong, and
dumps whole files into the context. These tools do the same work in-process,
read only the lines asked for, and write through the shared pipeline (history,
change journal, write rules) like ``workspace_write_file``.
"""

from __future__ import annotations

import fnmatch
import os
import re
import subprocess
import time
from typing import Any, Optional

from backend.bridge import resolve_workspace_path
from backend.server import mcp
from backend.util.json_util import tool_json
from backend.workspace.ai_ignore import ai_access_allowed, current_policy, require_ai_access

# Never walked into: VCS internals, dependencies, build output, UEFN's heavy dirs.
# A path given explicitly inside one of these is still read or searched.
_SKIP_DIRS = frozenset(
    {
        ".git", ".hg", ".svn", "node_modules", "__pycache__", ".venv", "venv", ".tox",
        ".pytest_cache", ".mypy_cache", ".ruff_cache", ".cache", ".next", ".turbo",
        "dist", "build", "target", "out", ".vs", ".idea",
        "Saved", "Intermediate", "DerivedDataCache", "Binaries",
    }
)
_BINARY_SUFFIXES = (
    ".uasset", ".umap", ".dll", ".exe", ".pdb", ".so", ".dylib", ".bin", ".zip", ".7z",
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".avif", ".ico", ".pdf", ".fbx", ".glb",
    ".wav", ".mp3", ".mp4", ".ogg", ".ttf", ".otf", ".woff", ".woff2", ".pyc", ".sqlite", ".db",
)
_MAX_FILE_BYTES = 2 * 1024 * 1024
_SEARCH_SECONDS = 10.0
_LINE_CHARS = 300
_GIT_OUTPUT_CHARS = 60_000
#: A read without a range stops here; the rest is a start_line away.
READ_LINE_CAP = 2000


def _project_root() -> str:
    return resolve_workspace_path(".")


def _rel(full: str, root: str) -> str:
    """Relative inside the open project; absolute for a file in another of the
    person's projects, so the path can be passed straight back to these tools
    (a ``../..`` path would be refused as escaping the project)."""
    try:
        rel = os.path.relpath(full, root)
    except ValueError:
        return full.replace("\\", "/")
    if rel == os.pardir or rel.startswith(os.pardir + os.sep):
        return full.replace("\\", "/")
    return rel.replace("\\", "/")


def _match_base(start: str, root: str) -> str:
    """What a folder glob (``src/**/*.py``) is relative to: the open project, or the
    folder searched when it is in another project."""
    if _rel(start, root) != start.replace("\\", "/"):
        return root
    return start if os.path.isdir(start) else os.path.dirname(start)


def _walk_files(start: str):
    """Every file under ``start`` (or ``start`` itself), skipping heavy dirs and binaries."""
    policy = current_policy()
    policy.require(start)
    if os.path.isfile(start):
        yield start
        return
    for dirpath, dirnames, filenames in os.walk(start):
        dirnames[:] = sorted(d for d in dirnames if d not in _SKIP_DIRS
                             and ai_access_allowed(os.path.join(dirpath, d), policy=policy))
        for name in sorted(filenames):
            if (not name.lower().endswith(_BINARY_SUFFIXES)
                    and ai_access_allowed(os.path.join(dirpath, name), policy=policy)):
                yield os.path.join(dirpath, name)


def _globs(spec: str) -> list[str]:
    return [g.strip().replace("\\", "/") for g in (spec or "").split(",") if g.strip()]


def _path_matches(rel: str, pattern: str) -> bool:
    """A glob on the project-relative path (``src/**/*.py``), or on the name alone when
    the pattern has no folder (``*.py``). A pattern without wildcards matches any path
    containing it."""
    pat = pattern.replace("\\", "/")
    low_rel, low_pat = rel.lower(), pat.lower()
    if not any(ch in pat for ch in "*?["):
        return low_pat in low_rel
    if "/" not in pat:
        return fnmatch.fnmatch(low_rel.rsplit("/", 1)[-1], low_pat)
    # ``**/`` also matches no folder at all: src/**/*.py covers src/a.py.
    variants = {low_pat, low_pat.replace("/**/", "/")}
    if low_pat.startswith("**/"):
        variants.add(low_pat[3:])
    return any(fnmatch.fnmatch(low_rel, v) for v in variants)


def _read_text(full: str) -> Optional[str]:
    """A text file's contents (None for binary or over 2 MB)."""
    require_ai_access(full)
    try:
        if os.path.getsize(full) > _MAX_FILE_BYTES:
            return None
        with open(full, "rb") as fh:
            raw = fh.read()
    except OSError:
        return None
    if b"\0" in raw[:8192]:
        return None
    return raw.decode("utf-8", errors="replace")


def _require_file(path: str) -> str:
    full = resolve_workspace_path(path)
    require_ai_access(full)
    if not os.path.isfile(full):
        raise ValueError(f"Not a file: {path}. Find it with workspace_find.")
    return full


def path_arg(relative_path: str, path: str) -> str:
    """The file a tool works on: relative_path, or path= (workspace_read_file takes both,
    so agents send either one to the edit tools too)."""
    rel, alias = (relative_path or "").strip(), (path or "").strip()
    if rel and alias and rel.replace("\\", "/") != alias.replace("\\", "/"):
        raise ValueError("relative_path and path name different files: pass only one of them")
    if not rel and not alias:
        raise ValueError("relative_path is required (path= also accepted)")
    return rel or alias


def read_lines(full: str, start_line: int = 0, end_line: int = 0, line_numbers: bool = False) -> dict[str, Any]:
    """Lines ``start_line``..``end_line`` (1-based, inclusive) of a text file. Without a
    range, the first READ_LINE_CAP lines, and a note of where the rest starts."""
    require_ai_access(full)
    with open(full, encoding="utf-8", errors="replace") as fh:
        text = fh.read()
    lines = text.splitlines()
    total = len(lines)
    first = max(1, int(start_line or 1))
    last = int(end_line or 0)
    capped = False
    if last <= 0:
        last = min(total, first + READ_LINE_CAP - 1)
        capped = last < total
    last = min(last, total)
    chosen = lines[first - 1:last] if first <= total else []
    if line_numbers:
        width = len(str(last or 1))
        body = "\n".join(f"{n:>{width}}| {ln}" for n, ln in zip(range(first, last + 1), chosen))
    elif first == 1 and last >= total:
        body = text  # the whole file, byte for byte (line endings, final newline)
    else:
        body = "\n".join(chosen)
    out: dict[str, Any] = {"content": body, "total_lines": total}
    if first > 1 or last < total:
        out["start_line"], out["end_line"] = first, last
    if capped:
        out["more"] = f"Lines {last + 1}-{total} not shown: read them with start_line={last + 1}."
    out["_full_text"] = text
    return out


@mcp.tool()
def workspace_read_files(
    paths: list[str],
    start_line: int = 0,
    end_line: int = 0,
    line_numbers: bool = False,
    pretty: bool = False,
) -> str:
    """Read several text files in one call (up to 20). Use this instead of a shell cat / Get-Content loop.

    start_line / end_line (1-based, inclusive) apply to every file; without them each file
    shows its first 2000 lines. line_numbers=true prefixes each line with its number.
    Read a big file's outline first (workspace_file_outline), then only the lines you need.
    Paths are relative to the open project, or absolute in any of the person's other Ducky projects.
    """
    if not paths:
        raise ValueError("paths is required: a list of file paths (project-relative or absolute)")
    root = _project_root()
    files: list[dict[str, Any]] = []
    for path in list(paths)[:20]:
        try:
            full = _require_file(path)
            part = read_lines(full, start_line, end_line, line_numbers)
            part.pop("_full_text", None)
            files.append({"path": _rel(full, root), **part})
        except (ValueError, OSError) as exc:
            files.append({"path": str(path), "error": str(exc)})
    out: dict[str, Any] = {"files": files}
    if len(paths) > 20:
        out["more"] = f"Only the first 20 of {len(paths)} paths were read."
    return tool_json(out, pretty=pretty)


# Definitions per language: (regex, kind). The name is the last group that matched.
_OUTLINE_RULES: dict[tuple[str, ...], list[tuple[re.Pattern[str], str]]] = {
    (".py",): [
        (re.compile(r"^(\s*)class\s+(\w+)"), "class"),
        (re.compile(r"^(\s*)(?:async\s+)?def\s+(\w+)"), "function"),
    ],
    (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs"): [
        (re.compile(r"^(\s*)(?:export\s+)?(?:default\s+)?(?:abstract\s+)?class\s+(\w+)"), "class"),
        (re.compile(r"^(\s*)(?:export\s+)?(?:default\s+)?(?:async\s+)?function\*?\s+(\w+)"), "function"),
        (re.compile(r"^(\s*)(?:export\s+)?(?:declare\s+)?(?:interface|type|enum)\s+(\w+)"), "type"),
        (re.compile(r"^()(?:export\s+)?(?:const|let)\s+(\w+)\s*(?::[^=]+)?=\s*(?:async\s*)?(?:\([^)]*\)|\w+)\s*=>"), "function"),
        (re.compile(r"^(\s+)(?:public\s+|private\s+|protected\s+|static\s+|async\s+|readonly\s+)*(\w+)\s*\([^)]*\)\s*(?::[^{]+)?\{\s*$"), "method"),
    ],
    (".rs",): [
        (re.compile(r"^(\s*)(?:pub(?:\([^)]*\))?\s+)?(?:async\s+)?(?:unsafe\s+)?fn\s+(\w+)"), "function"),
        (re.compile(r"^(\s*)(?:pub(?:\([^)]*\))?\s+)?(?:struct|enum|trait|union|type)\s+(\w+)"), "type"),
        (re.compile(r"^(\s*)impl(?:<[^>]*>)?\s+(?:[\w:<>, ]+\s+for\s+)?([\w:]+)"), "impl"),
        (re.compile(r"^(\s*)(?:pub(?:\([^)]*\))?\s+)?mod\s+(\w+)"), "module"),
    ],
    (".go",): [
        (re.compile(r"^()func\s+(?:\([^)]*\)\s*)?(\w+)"), "function"),
        (re.compile(r"^()type\s+(\w+)"), "type"),
    ],
    (".cs", ".java", ".kt", ".swift"): [
        (re.compile(r"^(\s*)(?:[\w\[\]]+\s+)*(?:class|interface|enum|struct|record|object)\s+(\w+)"), "class"),
        (re.compile(r"^(\s+)(?:public|private|protected|internal|static|override|virtual|async|fun|func|final)\s[^=;(]*?(\w+)\s*\("), "method"),
    ],
    (".c", ".h", ".cpp", ".hpp", ".cc"): [
        (re.compile(r"^()(?:class|struct|enum|union)\s+(\w+)"), "type"),
        (re.compile(r"^()[\w:<>*&\s]+?\b(\w+)\s*\([^;]*\)\s*(?:const\s*)?\{?\s*$"), "function"),
    ],
    (".verse",): [
        (re.compile(r"^(\s*)(\w+)(?:<[^>]*>)?\s*:=\s*(?:class|struct|interface|enum|module)\b"), "type"),
        (re.compile(r"^(\s*)(\w+)(?:<[^>]*>)?\s*\([^)]*\)\s*(?:<[^>]*>\s*)*:\s*[\w\[\]?]+\s*="), "function"),
    ],
    (".md", ".markdown"): [
        (re.compile(r"^()#{1,6}\s+(.+?)\s*#*\s*$"), "heading"),
    ],
}


@mcp.tool()
def workspace_file_outline(path: str, pretty: bool = False) -> str:
    """A file's outline: its classes, functions, methods and types (or a Markdown file's
    headings) with the lines each spans. Read this first, then only the lines you need
    with workspace_read_file(start_line, end_line), instead of reading a whole big file.
    Covers Python, TypeScript/JavaScript, Rust, Go, C#/Java/Kotlin/Swift, C/C++, Verse and Markdown.
    """
    full = _require_file(path)
    ext = os.path.splitext(full)[1].lower()
    rules = next((r for exts, r in _OUTLINE_RULES.items() if ext in exts), None)
    text = _read_text(full)
    if text is None:
        raise ValueError(f"{path} is binary or over 2 MB")
    lines = text.splitlines()
    if rules is None:
        return tool_json({"path": _rel(full, _project_root()), "total_lines": len(lines), "symbols": [],
                          "note": f"No outline for {ext or 'this file type'}: use workspace_search or read a line range."},
                         pretty=pretty)
    symbols: list[dict[str, Any]] = []
    for number, line in enumerate(lines, start=1):
        for rx, kind in rules:
            m = rx.match(line)
            if m and m.group(2) not in ("if", "for", "while", "switch", "return", "catch", "elif", "else"):
                symbols.append({"line": number, "kind": kind, "name": m.group(2).strip()[:120],
                                "indent": len(m.group(1).expandtabs(4)), "signature": line.strip()[:160]})
                break
    # A symbol ends where the next one at the same or a shallower depth starts.
    for i, sym in enumerate(symbols):
        end = len(lines)
        for nxt in symbols[i + 1:]:
            if nxt["indent"] <= sym["indent"]:
                end = nxt["line"] - 1
                break
        while end > sym["line"] and not lines[end - 1].strip():
            end -= 1
        sym["end_line"] = end
    for sym in symbols:
        sym.pop("indent", None)
    return tool_json({"path": _rel(full, _project_root()), "total_lines": len(lines), "symbols": symbols[:400]},
                     pretty=pretty)


@mcp.tool()
def workspace_tree(path: str = ".", depth: int = 3, max_entries: int = 400, pretty: bool = False) -> str:
    """A folder tree (folders end with /), down to ``depth`` levels. Use this instead of
    shell tree / dir /s / Get-ChildItem -Recurse. Skips .git, node_modules, build output,
    Saved/Intermediate. Folders deeper than ``depth`` show how many entries they hold.
    path: a folder in the open project, or an absolute path in any of the person's other Ducky projects.
    """
    start = resolve_workspace_path(path or ".")
    if not os.path.isdir(start):
        raise ValueError(f"Not a folder: {path}")
    depth = max(1, min(int(depth or 3), 8))
    limit = max(10, min(int(max_entries or 400), 3000))
    policy = current_policy()
    rows: list[str] = []
    truncated = False

    def walk(folder: str, level: int) -> None:
        nonlocal truncated
        try:
            names = sorted(os.listdir(folder), key=lambda n: (not os.path.isdir(os.path.join(folder, n)), n.lower()))
        except OSError:
            return
        for name in names:
            if len(rows) >= limit:
                truncated = True
                return
            full = os.path.join(folder, name)
            if not ai_access_allowed(full, policy=policy):
                continue
            pad = "  " * level
            if os.path.isdir(full):
                if name in _SKIP_DIRS:
                    continue
                if level + 1 >= depth:
                    try:
                        count = sum(ai_access_allowed(os.path.join(full, n), policy=policy)
                                    for n in os.listdir(full))
                    except OSError:
                        count = 0
                    rows.append(f"{pad}{name}/ ({count} entries)")
                else:
                    rows.append(f"{pad}{name}/")
                    walk(full, level + 1)
            elif not name.lower().endswith(_BINARY_SUFFIXES) or level == 0:
                rows.append(f"{pad}{name}")

    walk(start, 0)
    out: dict[str, Any] = {"path": _rel(start, _project_root()), "tree": "\n".join(rows)}
    if truncated:
        out["truncated"] = f"Stopped at {limit} entries: list a subfolder or lower depth."
    return tool_json(out, pretty=pretty)


@mcp.tool()
def workspace_search(
    pattern: str,
    path: str = ".",
    glob: str = "",
    regex: bool = False,
    case_sensitive: bool = False,
    context: int = 0,
    output_mode: str = "content",
    max_results: int = 100,
    pretty: bool = False,
) -> str:
    """Search text in project files (like ripgrep). Use this instead of a shell grep, Select-String or git grep.

    pattern: the text to find (regex=true for a regular expression). path: a folder or file
    in the open project (default: all of it), or an absolute path in any of the person's
    other Ducky projects. glob: only these files, e.g. "*.py" or
    "src/**/*.ts" (comma-separated for several). context: lines shown before and after each hit.
    output_mode: "content" (matching lines), "files" (just the files, with hit counts) or
    "count" (totals) — "files" first is cheapest on a broad search.
    Skips .git, node_modules, build output, Saved/Intermediate and binary files.
    """
    if not (pattern or "").strip():
        raise ValueError("pattern is required")
    mode = (output_mode or "content").strip().lower()
    if mode not in ("content", "files", "count"):
        raise ValueError('output_mode is "content", "files" or "count"')
    flags = 0 if case_sensitive else re.IGNORECASE
    try:
        rx = re.compile(pattern if regex else re.escape(pattern), flags)
    except re.error as exc:
        raise ValueError(f"Not a valid regular expression: {exc}. Pass regex=false to search the plain text.") from exc
    root = _project_root()
    start = resolve_workspace_path(path or ".")
    base = _match_base(start, root)
    globs = _globs(glob)
    context = max(0, min(int(context or 0), 10))
    limit = max(1, min(int(max_results or 100), 1000))
    deadline = time.monotonic() + _SEARCH_SECONDS
    matches: list[dict[str, Any]] = []
    files: list[dict[str, Any]] = []
    total_hits = files_searched = 0
    truncated = ""
    for full in _walk_files(start):
        if time.monotonic() > deadline:
            truncated = f"Stopped after {int(_SEARCH_SECONDS)} s: narrow path or glob."
            break
        rel = _rel(full, root)
        if globs and not any(_path_matches(_rel(full, base), g) for g in globs):
            continue
        text = _read_text(full)
        if text is None:
            continue
        files_searched += 1
        if not rx.search(text):
            continue
        lines = text.splitlines()
        hits_here = 0
        for i, line in enumerate(lines):
            if not rx.search(line):
                continue
            hits_here += 1
            if mode != "content":
                continue
            hit: dict[str, Any] = {"path": rel, "line": i + 1, "text": line[:_LINE_CHARS]}
            if context:
                hit["before"] = [ln[:_LINE_CHARS] for ln in lines[max(0, i - context):i]]
                hit["after"] = [ln[:_LINE_CHARS] for ln in lines[i + 1:i + 1 + context]]
            matches.append(hit)
            if len(matches) >= limit:
                truncated = f"Stopped at {limit} matches: narrow path or glob, or raise max_results."
                break
        total_hits += hits_here
        if hits_here:
            files.append({"path": rel, "hits": hits_here})
        if truncated or (mode == "files" and len(files) >= limit):
            truncated = truncated or f"Stopped at {limit} files: narrow path or glob, or raise max_results."
            break
    out: dict[str, Any] = {"files_searched": files_searched}
    if mode == "content":
        out["matches"] = matches
    elif mode == "files":
        out["files"] = files
    out["total_hits"], out["files_with_hits"] = total_hits, len(files)
    if truncated:
        out["truncated"] = truncated
    return tool_json(out, pretty=pretty)


@mcp.tool()
def workspace_find(pattern: str = "*", path: str = ".", max_results: int = 200, pretty: bool = False) -> str:
    """Find files by name or path pattern (like a glob or git ls-files). Use this instead of shell dir / Get-ChildItem listings.

    pattern: "*.py", "**/test_*.py", "src/**/mcp*", or a plain word (any path containing it).
    path: a folder in the open project (default: all of it), or an absolute path in any of the
    person's other Ducky projects. Returns paths relative to the open project, absolute elsewhere.
    Skips .git, node_modules, build output, Saved/Intermediate and binary files.
    """
    root = _project_root()
    start = resolve_workspace_path(path or ".")
    base = _match_base(start, root)
    limit = max(1, min(int(max_results or 200), 2000))
    deadline = time.monotonic() + _SEARCH_SECONDS
    found: list[str] = []
    truncated = ""
    for full in _walk_files(start):
        if time.monotonic() > deadline:
            truncated = f"Stopped after {int(_SEARCH_SECONDS)} s: narrow path or pattern."
            break
        if _path_matches(_rel(full, base), pattern or "*"):
            found.append(_rel(full, root))
            if len(found) >= limit:
                truncated = f"Stopped at {limit} files: narrow path or pattern, or raise max_results."
                break
    out: dict[str, Any] = {"files": found, "count": len(found)}
    if truncated:
        out["truncated"] = truncated
    return tool_json(out, pretty=pretty)


# ----------------------------------------------------------------------------- edits


def _write_edit(relative_path: str, before: str, after: str, tool: str, extra: dict[str, Any], pretty: bool = False) -> str:
    from backend.workspace.paths import content_hash
    from backend.workspace.runtime import get_writer

    # Edits work on the file as read (\n lines); a CRLF file is saved back as CRLF.
    if _uses_crlf(relative_path):
        after = after.replace("\r\n", "\n").replace("\n", "\r\n")
    result = get_writer().write_text(relative_path, after, expected_hash=content_hash(before), tool=tool)
    payload: dict[str, Any] = {
        "path": result.abs_path,
        "relative_path": result.path,
        **extra,
        "lines_added": result.lines_added,
        "lines_removed": result.lines_removed,
    }
    more = result.to_payload()
    for key in ("changeset", "in_lane", "warning"):
        if key in more:
            payload[key] = more[key]
    return tool_json(payload, pretty=pretty)


def _current_text(relative_path: str) -> str:
    # Read the way the write pipeline does, so its stale-write guard compares like for like.
    with open(_require_file(relative_path), encoding="utf-8", errors="replace") as fh:
        return fh.read()


def _uses_crlf(relative_path: str) -> bool:
    with open(_require_file(relative_path), "rb") as fh:
        return b"\r\n" in fh.read(65536)


def _apply_edit(text: str, old_text: str, new_text: str, replace_all: bool, label: str = "") -> tuple[str, int]:
    if not old_text:
        lines = len(text.splitlines())
        raise ValueError(
            f"{label}old_text is required. To add text at the end, use workspace_replace_lines with "
            f"start_line={lines + 1}, end_line={lines}; to create or overwrite a whole file use workspace_write_file."
        )
    # The text is read with \n line breaks whatever the file uses on disk.
    old_text, new_text = old_text.replace("\r\n", "\n"), new_text.replace("\r\n", "\n")
    if old_text == new_text:
        raise ValueError(f"{label}old_text and new_text are the same: nothing to change")
    count = text.count(old_text)
    if count == 0:
        raise ValueError(
            f"{label}old_text is not in the file. Read the exact lines with workspace_read_file "
            "(start_line/end_line) and copy them, spaces included."
        )
    if count > 1 and not replace_all:
        raise ValueError(f"{label}old_text appears {count} times. Add surrounding lines so it is unique, or pass replace_all=true.")
    return (text.replace(old_text, new_text) if replace_all else text.replace(old_text, new_text, 1)), (count if replace_all else 1)


@mcp.tool()
def workspace_edit_file(
    relative_path: str = "",
    *,
    old_text: str,
    new_text: str,
    replace_all: bool = False,
    pretty: bool = False,
    path: str = "",
) -> str:
    """Replace exact text in a project file (an editor's find and replace). Use this instead of rewriting the whole file or editing through a shell.

    old_text must match the file exactly, spaces and line breaks included, and appear once
    (add surrounding lines to make it unique, or pass replace_all=true). To add lines at the
    end, use workspace_replace_lines instead. Same write rules, history and change journal
    as workspace_write_file.
    """
    relative_path = path_arg(relative_path, path)
    text = _current_text(relative_path)
    updated, count = _apply_edit(text, old_text, new_text, replace_all)
    return _write_edit(relative_path, text, updated, "workspace_edit_file", {"replacements": count}, pretty)


@mcp.tool()
def workspace_multi_edit(
    relative_path: str = "", *, edits: list[dict[str, Any]], pretty: bool = False, path: str = ""
) -> str:
    """Several exact-text replacements in one file, applied in order and saved together (all or none).

    edits: [{"old_text": "...", "new_text": "...", "replace_all": false}, ...]. Each old_text
    must match the file as the earlier edits left it. One history entry for the whole change.
    """
    if not edits:
        raise ValueError("edits is required: a list of {old_text, new_text}")
    relative_path = path_arg(relative_path, path)
    text = _current_text(relative_path)
    updated, total = text, 0
    for n, edit in enumerate(edits, start=1):
        if not isinstance(edit, dict):
            raise ValueError(f"edit {n} must be an object with old_text and new_text")
        updated, count = _apply_edit(updated, str(edit.get("old_text") or ""), str(edit.get("new_text") or ""),
                                     bool(edit.get("replace_all")), label=f"edit {n}: ")
        total += count
    return _write_edit(relative_path, text, updated, "workspace_multi_edit", {"edits": len(edits), "replacements": total}, pretty)


@mcp.tool()
def workspace_replace_lines(
    relative_path: str = "", *, start_line: int, end_line: int, new_text: str, pretty: bool = False, path: str = ""
) -> str:
    """Replace lines start_line..end_line (1-based, inclusive) of a project file with new_text,
    without repeating the old text. To insert before line N, pass start_line=N and end_line=N-1
    (to add at the end of a file with L lines: start_line=L+1, end_line=L, so 1 and 0 for an
    empty file); to delete lines, pass new_text="". Read the lines first (workspace_read_file
    with line_numbers=true) so the numbers are current.
    """
    relative_path = path_arg(relative_path, path)
    text = _current_text(relative_path)
    lines = text.splitlines(keepends=True)
    total = len(lines)
    start, end = int(start_line), int(end_line)
    if start < 1 or start > total + 1 or end < start - 1 or end > total:
        raise ValueError(f"Line range {start}-{end} is outside the file (1-{total}).")
    block = new_text.replace("\r\n", "\n")
    if block and not block.endswith("\n") and (end < total or text.endswith("\n")):
        block += "\n"
    if block and start == total + 1 and text and not text.endswith("\n"):
        block = "\n" + block  # appended lines start a new line, not the end of the last one
    updated = "".join(lines[:start - 1]) + block + "".join(lines[end:])
    return _write_edit(relative_path, text, updated, "workspace_replace_lines",
                       {"replaced_lines": f"{start}-{end}" if end >= start else f"inserted before {start}"}, pretty)


@mcp.tool()
def workspace_move_file(source: str, destination: str, pretty: bool = False) -> str:
    """Move or rename a project file or folder (destination: the new project-relative path).
    Undoable from the Changes view like an edit; never overwrites an existing destination.
    """
    from frontend.ui_web import project_files

    src = source.strip().replace("\\", "/").strip("/")
    dst = destination.strip().replace("\\", "/").strip("/")
    if not src or not dst:
        raise ValueError("source and destination are required")
    from backend.workspace.ai_ignore import require_ai_path_operation
    require_ai_path_operation(resolve_workspace_path(src), resolve_workspace_path(dst))
    src_parent, _, src_name = src.rpartition("/")
    dst_parent, _, dst_name = dst.rpartition("/")
    intermediate = "/".join(p for p in (dst_parent, src_name) if p)
    from backend.workspace.runtime import get_writer

    # A move followed by a rename must not relocate the source before finding
    # that the final destination is out of lane. Each step also rechecks policy.
    paths = (src, intermediate, dst) if dst_parent != src_parent else (src, dst)
    get_writer().preflight_paths("move", paths, tool="workspace_move_file")
    path = src
    if dst_parent != src_parent:
        require_ai_path_operation(resolve_workspace_path(src), resolve_workspace_path(intermediate))
        path = project_files.move_project_entry(src, dst_parent)["path"]
    if dst_name != src_name:
        path = project_files.rename_project_entry(path, dst_name)["path"]
    return tool_json({"path": path, "from": src}, pretty=pretty)


@mcp.tool()
def workspace_delete_file(relative_path: str = "", pretty: bool = False, path: str = "") -> str:
    """Delete a project file or folder. It goes to Ducky's undo trash (restorable from the
    Changes view), so prefer this to a shell rm / Remove-Item.
    """
    from frontend.ui_web import project_files

    relative_path = path_arg(relative_path, path)

    from backend.workspace.ai_ignore import require_ai_path_operation
    require_ai_path_operation(resolve_workspace_path(relative_path))
    out = project_files.delete_project_entry(relative_path)
    return tool_json({"path": out.get("path"), "deleted": True, "restorable": True}, pretty=pretty)


# ----------------------------------------------------------------------------- git

# Read-only git. Anything that changes the repo stays in the shell, where it asks first.
_GIT_READ_COMMANDS = frozenset(
    {"status", "diff", "log", "show", "ls-files", "blame", "branch", "rev-parse", "shortlog", "grep", "describe", "tag"}
)
# Writes a file, runs a program, or reads a file outside the repo (--no-index, blame --contents).
_GIT_BLOCKED_ARGS = ("--output", "--open-files-in-pager", "--ext-diff", "--exec", "--no-index", "--contents", "-c")
# branch / tag only list: these flags (or a bare name) would create, move or delete one.
_GIT_LIST_ONLY_FLAGS = frozenset(
    {"-a", "--all", "-r", "--remotes", "-v", "-vv", "--verbose", "--list", "-l", "--show-current",
     "--merged", "--no-merged", "--contains", "--no-contains", "--points-at", "--sort", "-n",
     "--format", "--column", "--no-column", "--color", "--no-color"}
)
_GIT_VALUE_FLAGS = ("--contains", "--no-contains", "--merged", "--no-merged", "--points-at", "--sort", "--format")


def _git_args_refused(command: str, args: list[str]) -> str:
    for arg in args:
        name = arg.split("=", 1)[0]
        # git accepts any unambiguous abbreviation of a long option (--open-files, --out=...).
        if any(name == flag or (flag.startswith("--") and len(name) > 2 and flag.startswith(name))
               for flag in _GIT_BLOCKED_ARGS) or arg.startswith("-O"):
            return f"{arg} is not allowed here"
    if command in ("branch", "tag"):
        expects_value = False
        for arg in args:
            if expects_value:
                expects_value = False
                continue
            flag = arg.split("=", 1)[0]
            if flag in _GIT_VALUE_FLAGS:
                expects_value = "=" not in arg
                continue
            if flag not in _GIT_LIST_ONLY_FLAGS:
                return f"git {command} {arg} would change the repo: only listing is allowed here"
    return ""


@mcp.tool()
def workspace_git(command: str, args: Optional[list[str]] = None, path: str = ".", pretty: bool = False) -> str:
    """Read-only git in the project: status, diff, log, show, ls-files, blame, branch, grep, rev-parse, shortlog, describe, tag. Use this instead of running git through a shell.

    path: where to run it, in the open project (default) or an absolute path in any of the
    person's other Ducky projects (that folder's repository).
    args: the rest of the command line as a list, e.g. command="log", args=["--oneline", "-20"]
    or command="diff", args=["HEAD", "--", "src/app.py"]. Commits, pushes and other changes
    stay in the shell, where they ask first.
    """
    # Git objects/history and external diff helpers bypass path denials. Never
    # run git on behalf of AI in strict mode; use guarded workspace tools.
    if current_policy().strict:
        raise ValueError("AI_FILE_PROTECTION: git is blocked in strict protection mode.")
    cmd = (command or "").strip().lower()
    if cmd not in _GIT_READ_COMMANDS:
        raise ValueError(
            f"git {cmd or '(none)'} is not a read: allowed are {', '.join(sorted(_GIT_READ_COMMANDS))}. "
            "Use your shell for commits, pushes and other changes (they ask first)."
        )
    argv = [str(a) for a in (args or [])]
    refused = _git_args_refused(cmd, argv)
    if refused:
        raise ValueError(refused)
    cwd = resolve_workspace_path(path or ".")
    if os.path.isfile(cwd):
        cwd = os.path.dirname(cwd)
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0", "GIT_PAGER": "cat", "PAGER": "cat", "GIT_OPTIONAL_LOCKS": "0"}
    try:
        proc = subprocess.run(
            ["git", "-c", "core.quotepath=off", "--no-pager", cmd, *argv],
            cwd=cwd, env=env, stdin=subprocess.DEVNULL, capture_output=True, timeout=30,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except FileNotFoundError as exc:
        raise ValueError("git is not installed on this PC (or not on PATH).") from exc
    except subprocess.TimeoutExpired as exc:
        raise ValueError(f"git {cmd} took over 30 s: narrow it (a path, -n, a range).") from exc
    out = proc.stdout.decode("utf-8", errors="replace")
    err = proc.stderr.decode("utf-8", errors="replace").strip()
    payload: dict[str, Any] = {"exit_code": proc.returncode, "output": out[:_GIT_OUTPUT_CHARS]}
    if len(out) > _GIT_OUTPUT_CHARS:
        payload["truncated"] = f"Output cut at {_GIT_OUTPUT_CHARS} characters: narrow it (a path, -n, --stat)."
    if err:
        payload["stderr"] = err[:4000]
    return tool_json(payload, pretty=pretty)
