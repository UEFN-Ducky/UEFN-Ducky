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
import functools
import os
import re
import shutil
import subprocess
import threading
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
#: One search or find call, git and folder walk together, returns what it has by then.
_SEARCH_SECONDS = 20.0
_LINE_CHARS = 300
_GIT_OUTPUT_CHARS = 60_000
#: A read without a range stops here; the rest is a start_line away.
READ_LINE_CAP = 2000
#: One read never returns more text than this (~50k tokens), whatever the line range: a
#: minified bundle is a few lines of hundreds of KB each.
READ_CHAR_CAP = 200_000
#: A multi-file read or a search answers inline up to this much text. Past it the result goes
#: to a spill file the agent pages with ducky_read_tool_spill: an agent CLI that saves an
#: oversized result itself puts it where these tools cannot read it back.
INLINE_RESULT_CHARS = 30_000


def _inline_or_spill(tool: str, text: str) -> str:
    from backend.agent.tool_spills import inline_or_spill

    return inline_or_spill(tool, text, limit=INLINE_RESULT_CHARS)


def chat_file_path(raw: str) -> str:
    """The absolute path when ``raw`` is a file saved in a chat (attachments, screenshots),
    else "". Those live in Ducky's own data folder, outside every project; reads only."""
    if not raw or not os.path.isabs(raw):
        return ""
    full = os.path.realpath(os.path.abspath(raw))
    try:
        from frontend.settings import default_app_data_dir

        chats = os.path.realpath(str(default_app_data_dir() / "chats"))
    except Exception:
        return ""
    if os.path.commonpath([os.path.normcase(full), os.path.normcase(chats)]) != os.path.normcase(chats):
        return ""
    require_ai_access(full)
    return full


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


def _relto(full: str, base: str) -> str:
    """``full`` relative to ``base`` with / separators; absolute when it is not under it."""
    try:
        rel = os.path.relpath(full, base)
    except ValueError:
        return full.replace("\\", "/")
    if rel == os.pardir or rel.startswith(os.pardir + os.sep):
        return full.replace("\\", "/")
    return "." if rel == os.curdir else rel.replace("\\", "/")


@functools.lru_cache(maxsize=256)
def _glob_regex(pat: str) -> re.Pattern[str]:
    """A lower-case folder glob read the way git reads one: ``*`` and ``?`` stay inside a
    folder, ``**`` crosses folders and ``**/`` also matches no folder (src/**/*.py covers src/a.py)."""
    out, i = [], 0
    while i < len(pat):
        if pat.startswith("**/", i):
            out.append("(?:.*/)?")
            i += 3
        elif pat.startswith("/**", i) and i + 3 == len(pat):
            out.append("(?:/.*)?")
            i += 3
        elif pat.startswith("**", i):
            out.append(".*")
            i += 2
        elif pat[i] in "*?":
            out.append("[^/]*" if pat[i] == "*" else "[^/]")
            i += 1
        elif pat[i] == "[" and pat.find("]", i + 2) > 0:
            end = pat.find("]", i + 2)
            body = pat[i + 1:end]
            out.append("[" + ("^" + body[1:] if body.startswith("!") else body).replace("\\", "\\\\") + "]")
            i = end + 1
        else:
            out.append(re.escape(pat[i]))
            i += 1
    return re.compile("".join(out) + r"\Z")


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
        return fnmatch.fnmatchcase(low_rel.rsplit("/", 1)[-1], low_pat)
    return bool(_glob_regex(low_pat.lstrip("/")).match(low_rel))


def _folder_may_hold(rel_dir: str, globs: list[str]) -> bool:
    """False when no glob can match a file under ``rel_dir`` (relative to the glob base), so
    the walk skips that folder without listing it."""
    if not globs or rel_dir in ("", ".") or os.path.isabs(rel_dir) or rel_dir.startswith(".."):
        return True
    parts = rel_dir.lower().split("/")
    for g in globs:
        pat = g.lower().lstrip("/")
        if "/" not in pat or not any(ch in pat for ch in "*?["):
            return True  # a name glob or a plain word can match in any folder
        folders = pat.split("/")[:-1]
        for i, part in enumerate(parts):
            if i >= len(folders) or ("**" not in folders[i] and not fnmatch.fnmatchcase(part, folders[i])):
                break
            if "**" in folders[i]:
                return True
        else:
            return True
    return False


class _GitIgnore:
    """One folder's .gitignore. ``!`` re-includes are skipped, so this can only hide more."""

    def __init__(self, folder: str) -> None:
        self.folder = folder
        self.rules: list[tuple[str, bool, bool]] = []  # (pattern, anchored, folders only)
        try:
            with open(os.path.join(folder, ".gitignore"), encoding="utf-8", errors="replace") as fh:
                lines = fh.read().splitlines()
        except OSError:
            return
        for raw in lines:
            line = raw.strip()
            if not line or line.startswith(("#", "!")):
                continue
            dir_only = line.endswith("/")
            line = line.rstrip("/")
            if line:
                self.rules.append((line.lstrip("/").lower(), "/" in line, dir_only))

    def ignored(self, full: str, is_dir: bool) -> bool:
        if not self.rules:
            return False
        rel = _relto(full, self.folder)
        if os.path.isabs(rel) or rel == ".":
            return False
        low = rel.lower()
        name = low.rsplit("/", 1)[-1]
        for pat, anchored, dir_only in self.rules:
            if dir_only and not is_dir:
                continue
            if _glob_regex(pat).match(low) if anchored else fnmatch.fnmatchcase(name, pat):
                return True
        return False


class _TimeUp(Exception):
    """The search's time limit passed."""


def _walk_files(start: str, base: str = "", globs: Optional[list[str]] = None, policy: Any = None,
                deadline: Optional[float] = None):
    """Files under ``start`` (or ``start`` itself) that ``globs`` (relative to ``base``) can
    match, skipping heavy dirs, binaries, .gitignore'd paths (base's and start's) and folders
    no glob can reach. AI-ignore is checked once per folder; callers check each file they
    return or read (a file rule, a link). Past ``deadline`` (monotonic) it raises _TimeUp,
    checked per entry: a walk through folders whose files no glob matches yields nothing
    for minutes, so a check between yielded files alone never fires."""
    policy = policy or current_policy()
    policy.require(start)
    if os.path.isfile(start):
        yield start
        return
    base = base or start
    globs = globs or []
    ignores = [_GitIgnore(base)]
    if os.path.normcase(os.path.normpath(start)) != os.path.normcase(os.path.normpath(base)):
        ignores.append(_GitIgnore(start))
    ignores = [ig for ig in ignores if ig.rules]
    stack = [start]
    while stack:
        folder = stack.pop()
        try:
            with os.scandir(folder) as it:
                entries = sorted(it, key=lambda e: e.name)
        except OSError:
            continue
        subdirs: list[str] = []
        for entry in entries:
            if deadline is not None and time.monotonic() > deadline:
                raise _TimeUp
            try:
                is_dir = entry.is_dir()
            except OSError:
                continue
            full = entry.path
            if is_dir:
                if (entry.name in _SKIP_DIRS or any(ig.ignored(full, True) for ig in ignores)
                        or not _folder_may_hold(_relto(full, base), globs)
                        or not ai_access_allowed(full, policy=policy)):
                    continue
                subdirs.append(full)
            elif not entry.name.lower().endswith(_BINARY_SUFFIXES) and not any(ig.ignored(full, False) for ig in ignores):
                if not globs or any(_path_matches(_relto(full, base), g) for g in globs):
                    yield full
        stack.extend(reversed(subdirs))


def _git_env() -> dict[str, str]:
    return {**os.environ, "GIT_TERMINAL_PROMPT": "0", "GIT_PAGER": "cat", "PAGER": "cat", "GIT_OPTIONAL_LOCKS": "0"}


_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def _git(argv: list[str], cwd: str, timeout: float) -> Optional[subprocess.CompletedProcess]:
    try:
        return subprocess.run(["git", "-c", "core.quotepath=off", "--no-pager", *argv], cwd=cwd, env=_git_env(),
                              stdin=subprocess.DEVNULL, capture_output=True, timeout=timeout, creationflags=_NO_WINDOW)
    except (OSError, subprocess.SubprocessError):
        return None


def _git_scope(start: str, policy: Any) -> Optional[tuple[str, str]]:
    """(work tree root, ``start`` relative to it) when git can list ``start``: inside a work
    tree and not git-ignored. None outside git, in strict protection (git bypasses path
    rules), or when git is missing; callers walk the folder instead."""
    if policy.strict or shutil.which("git") is None:
        return None
    # The nearest folder holding .git (a folder, or a file in a worktree): a git rev-parse
    # costs a process start, 0.2 s on Windows.
    top = os.path.normpath(os.path.abspath(start if os.path.isdir(start) else os.path.dirname(start)))
    while not os.path.exists(os.path.join(top, ".git")):
        parent = os.path.dirname(top)
        if parent == top:
            return None
        top = parent
    srel = _relto(start, top)
    if os.path.isabs(srel):
        return None
    srel = "" if srel == "." else srel
    if srel:
        if ".git" in srel.lower().split("/"):
            return None
        # An explicit ignored folder (Saved/, node_modules/) holds nothing git would grep.
        ignored = _git(["check-ignore", "-q", "--", srel], top, 10)
        if ignored is None or ignored.returncode == 0:
            return None
    return top, srel


def _read_text(full: str, checked: bool = False) -> Optional[str]:
    """A text file's contents (None for binary or over 2 MB). ``checked``: the caller already
    ran the AI-ignore check on this path."""
    if not checked:
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


def _is_file_name(p: str) -> bool:
    return bool(os.path.splitext(p.rstrip("/").rsplit("/", 1)[-1])[1])


def path_arg(relative_path: str, path: str) -> str:
    """The file a tool works on: relative_path, or path= (workspace_read_file takes both,
    so agents send either one to the edit tools too). A folder in one and the file in the
    other is that file: the full path when it already starts with the folder, else joined."""
    rel, alias = (relative_path or "").strip(), (path or "").strip()
    if rel in (".", "./", ".\\"):  # "the project" next to a real path= means that path
        rel = ""
    if not rel and not alias:
        raise ValueError("relative_path is required (path= also accepted)")
    if not rel or not alias:
        return rel or alias
    a, b = rel.replace("\\", "/"), alias.replace("\\", "/")
    if a == b:
        return rel
    for longer, shorter in ((a, b), (b, a)):
        if longer.startswith(shorter.rstrip("/") + "/") or longer.endswith("/" + shorter):
            return longer
    # Oct 17 2026: relative_path="Content/DuckyAITest" + path="treasure_hunt_device.verse" was refused.
    if _is_file_name(a) != _is_file_name(b):
        folder, name = (b, a) if _is_file_name(a) else (a, b)
        if not os.path.isabs(name) and not re.match(r"^[A-Za-z]:", name):
            return folder.rstrip("/") + "/" + name
    raise ValueError("relative_path and path name different files: pass only one of them")


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
    long_line = False
    if len(body) > READ_CHAR_CAP:
        # Stop at the last whole line that fits; a single huge line is cut inside it.
        keep = body.rfind("\n", 0, READ_CHAR_CAP)
        if keep > 0:
            body = body[:keep]
            last = first + body.count("\n")
        else:
            body = body[:READ_CHAR_CAP]
            last = first
            long_line = True
        capped = True
    out: dict[str, Any] = {"content": body, "total_lines": total}
    if first > 1 or last < total:
        out["start_line"], out["end_line"] = first, last
    if long_line:
        rest = f" Read on with start_line={last + 1}." if last < total else ""
        out["more"] = f"Line {last} is longer than {READ_CHAR_CAP:,} characters and was cut.{rest}"
    elif capped and last < total:
        out["more"] = f"Lines {last + 1}-{total} not shown: read them with start_line={last + 1}."
    out["_full_text"] = text
    return out


def format_read(display: str, part: dict[str, Any]) -> str:
    """A ``read_lines`` result as text: a header line ``path  lines a-b of N``, then the lines,
    then the note on what is not shown."""
    total = int(part.get("total_lines") or 0)
    first, last = int(part.get("start_line", 1)), int(part.get("end_line", total))
    if total == 0:
        head = f"{display}  empty file"
    elif first > total:
        head = f"{display}  no lines: start_line {first} is past the end ({total} lines)"
    else:
        head = f"{display}  lines {first}-{last} of {total}"
    text = head + "\n" + str(part.get("content") or "")
    if part.get("more"):
        text = text.rstrip("\n") + f"\n[{part['more']}]"
    return text


@mcp.tool()
def workspace_read_files(
    paths: list[str],
    start_line: int = 0,
    end_line: int = 0,
    line_numbers: bool = False,
    format: str = "text",
    pretty: bool = False,
) -> str:
    """Read up to 20 text files in one call. Never use a shell cat / Get-Content loop for this.

    start_line / end_line (1-based, inclusive) apply to every file; without them each file
    shows its first 2000 lines. line_numbers=true prefixes each line with its number.
    Each file starts with a line "path  lines a-b of N", then its text.
    Read a big file's outline first (workspace_file_outline), then only the lines you need.
    Paths are relative to the open project, or absolute in any of the person's other Ducky projects.
    format="json" returns {files: [{path, content, total_lines, ...}]}.
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
    more = f"Only the first 20 of {len(paths)} paths were read." if len(paths) > 20 else ""
    if (format or "text").strip().lower() == "json":
        out: dict[str, Any] = {"files": files}
        if more:
            out["more"] = more
        return tool_json(out, pretty=pretty)
    blocks = [f"{f['path']}  ERROR: {f['error']}" if "error" in f else format_read(f["path"], f) for f in files]
    if more:
        blocks.append(f"[{more}]")
    return _inline_or_spill("workspace_read_files", "\n\n".join(b.rstrip("\n") for b in blocks))


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
    """List a file's classes, functions, types or headings with their line spans. Never returns the file's text.

    Read this first, then only the lines you need
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


_IMAGE_SUFFIXES = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp")
_MAX_IMAGE_BYTES = 20 * 1024 * 1024


def _capture_file(raw: str) -> str:
    """A picture straight inside Ducky's tool_captures folder: the "Capture file:" a chat
    message names next to the attachment copy."""
    try:
        from frontend.ui_web.tool_captures import tool_captures_dir

        full = os.path.realpath(raw)
        if os.path.dirname(full) == os.path.realpath(str(tool_captures_dir())):
            return full
    except Exception:
        pass
    return ""


def _image_path(path: str) -> str:
    """An image in the open project, another of the person's projects, or a chat's
    attachments (pictures the person sent, screenshots Ducky took)."""
    raw = (path or "").strip()
    if not raw:
        raise ValueError("path is required")
    if not raw.lower().endswith(_IMAGE_SUFFIXES):
        raise ValueError(f"Not a picture ({', '.join(_IMAGE_SUFFIXES)}): {raw!r}")
    full = chat_file_path(raw) or _capture_file(raw) or resolve_workspace_path(raw)
    if not os.path.isfile(full):
        raise ValueError(f"No such picture: {raw!r}")
    if os.path.getsize(full) > _MAX_IMAGE_BYTES:
        raise ValueError(f"Picture is over {_MAX_IMAGE_BYTES // (1024 * 1024)} MB: {raw!r}")
    return full


@mcp.tool()
def view_image(path: str, pretty: bool = False) -> Any:
    """Look at a picture: one the person attached, a screenshot Ducky saved, or an image file in
    a project (png, jpg, gif, webp, bmp). Pass the absolute path given in the chat, or a project
    path. Returns the picture itself, so you see it."""
    full = _image_path(path)
    out: dict[str, Any] = {"path": full.replace("\\", "/"), "bytes": os.path.getsize(full)}
    url = _picture_url(full)
    if url:
        out["media_url"] = url  # the chat's tool card shows the picture
    from mcp.server.fastmcp import Image

    return [tool_json(out, pretty=pretty), Image(path=full)]


_CHAT_PICTURE = re.compile(r"[\\/]conversations[\\/]([^\\/]+)[\\/]attachments[\\/]([^\\/]+\.(?:png|jpe?g|webp))$", re.IGNORECASE)


def _picture_url(full: str) -> str:
    """A link the chat's tool card can show: a chat's own attachment as it is, any other
    png/jpg/webp as a copy saved in the calling chat (the panel serves nothing else)."""
    try:
        from frontend.ui_web.conversation_attachments import build_chat_attachment_url, save_chat_screenshot

        hit = _CHAT_PICTURE.search(full)
        if hit:
            return build_chat_attachment_url(hit.group(1), hit.group(2))
        if not full.lower().endswith((".png", ".jpg", ".jpeg", ".webp")):
            return ""  # gif/bmp: the agent still sees it; the card shows the path
        with open(full, "rb") as fh:
            saved = save_chat_screenshot(fh.read(), prefix="viewed_" + os.path.splitext(os.path.basename(full))[0])
        return str(saved.get("media_url") or "") if saved.get("ok") else ""
    except Exception:
        return ""


@mcp.tool()
def ducky_generate_image(prompt: str, confirm_spend: bool = False, backend: str = "", size: str = "",
                         pretty: bool = False) -> Any:
    """Make a picture from a text prompt and show it in this chat. Uses the person's default
    picture maker (Settings > LLMs: Ducky AI, OpenAI, Google, Meshy...); backend= one backend id
    picks another. It is billed: ask the person first, then call with confirm_spend=true.
    size: e.g. "1024x1024" when the maker takes one. Returns the picture, so you see it."""
    import tempfile
    from pathlib import Path

    from backend.automations import media
    from frontend.ui_web import generated_images as gen

    text = str(prompt or "").strip()
    if not text:
        raise ValueError("Say what the picture shows (prompt).")
    row = media.pick_backend("image.generate", str(backend or "").strip())
    cfg: dict[str, Any] = {"backend": row["id"], "spend": bool(confirm_spend)}
    if size:
        cfg["gateway_config"] = {row["id"]: {"size": str(size)}}
    chat = gen._chat_of_caller()
    started = time.time()
    with tempfile.TemporaryDirectory(prefix="ducky-image-") as folder:
        step = media.run_media("image.generate", cfg, {"prompt": text}, Path(folder))
        if not step.get("ok"):
            if step.get("gate") and not confirm_spend:
                raise ValueError(f"{row['label']} costs {media.cost_text(row)} a picture. Ask the person, "
                                 "then call again with confirm_spend=true.")
            raise ValueError(str(step.get("error") or "No picture came back."))
        made = Path(step["outputs"]["image"]["path"])
        # A plugin tool already keeps its pictures for the chat that called it; never twice.
        kept = next((r for r in gen.list_generated_images()
                     if chat and r.get("conv_id") == chat and float(r.get("created") or 0) >= started), None)
        if kept is None:
            kept = gen.save_generated_image(made.read_bytes(), prompt=text, gateway=row["id"],
                                            filename=made.name, mime=gen._mime_for(made), conv_id=chat)
    from mcp.server.fastmcp import Image

    out = {"path": str(kept["path"]).replace("\\", "/"), "media_url": str(kept.get("media_url") or ""),
           "backend": row["label"], "maker": row.get("plugin"), "cost": media.cost_text(row)}
    return [tool_json(out, pretty=pretty), Image(path=str(kept["path"]))]


@mcp.tool()
def workspace_tree(path: str = ".", depth: int = 3, max_entries: int = 400, pretty: bool = False) -> str:
    """Show a folder tree down to ``depth`` levels. Never lists .git, node_modules, build output or Saved.

    Folders end with /. Use this instead of shell tree / dir /s / Get-ChildItem -Recurse.
    Folders deeper than ``depth`` show how many entries they hold.
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


class _Found:
    """What a search found: rows per file (line -> [text, is_match]), hits per file, and why
    it stopped early (``note``; empty means it searched everything it was asked to)."""

    def __init__(self) -> None:
        self.rows: dict[str, dict[int, list[Any]]] = {}
        self.counts: dict[str, int] = {}
        self.note = ""
        self.files_searched: Optional[int] = None
        self.timed_out = False

    def row(self, path: str, line: int, text: str, is_match: bool) -> None:
        rows = self.rows.setdefault(path, {})
        prev = rows.get(line)
        rows[line] = [text[:_LINE_CHARS], is_match or bool(prev and prev[1])]

    def hit(self, path: str, n: int = 1) -> None:
        self.counts[path] = self.counts.get(path, 0) + n


_GIT_ROW = re.compile(r"(\d+)([:=-])(.*)\Z", re.S)


def _kill_git(proc: subprocess.Popen) -> None:
    """Git for Windows' cmd\\git.exe is a launcher: killing only it leaves the real git.exe
    holding the pipes, so reading them never ends. Kill the whole tree."""
    if os.name == "nt":
        try:
            from backend.agent.coding_agents.proc_exec import _kill_windows_tree

            _kill_windows_tree(proc.pid)
        except Exception:  # noqa: BLE001
            pass
    proc.kill()


def _git_search(
    scope: tuple[str, str], start: str, base: str, root: str, globs: list[str], pattern: str,
    regex: bool, case_sensitive: bool, context: int, mode: str, limit: int, policy: Any,
    deadline: float,
) -> Optional[_Found]:
    """``git grep`` over tracked and untracked, not-ignored files, killed at ``deadline``.
    None when git can't run it (the caller walks instead)."""
    top, srel = scope
    flags = ["-I", "--untracked", "--no-color", "-n"]
    if not case_sensitive:
        flags.append("-i")
    if mode == "content":
        flags += ["--heading", "--break"] + (["-C", str(context)] if context else [])
    else:
        flags += ["-c", "--null"]
    specs = _git_pathspecs(top, srel, start, base, globs)
    for syntax in (["-P"], ["-E"]) if regex else (["-F"],):
        argv = ["git", "-c", "core.quotepath=off", "--no-pager", "grep", *flags, *syntax, "-e", pattern, "--", *specs]
        try:
            proc = subprocess.Popen(argv, cwd=top, env=_git_env(), stdin=subprocess.DEVNULL,
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, creationflags=_NO_WINDOW)
        except OSError:
            return None
        fired: list[bool] = []
        timer = threading.Timer(max(0.0, deadline - time.monotonic()), lambda: (fired.append(True), _kill_git(proc)))
        timer.start()
        try:
            found, read_any = _read_git_grep(proc, top, start, base, root, globs, mode, limit, policy)
            if found.note:
                _kill_git(proc)
            # stderr is only read after stdout ends: git writes little there, never enough to block.
            err = proc.stderr.read().decode("utf-8", errors="replace") if proc.stderr else ""
            proc.wait()
        finally:
            timer.cancel()
            for stream in (proc.stdout, proc.stderr):
                if stream:
                    stream.close()
        if found.note:
            return found
        if fired:
            found.timed_out = True
            found.note = (f"INCOMPLETE: time limit {int(_SEARCH_SECONDS)} s, git grep was stopped "
                          "— narrow path or pattern.")
            return found
        if proc.returncode in (0, 1):  # 1: nothing matched
            return found
        if read_any:
            found.note = f"INCOMPLETE: git grep failed part way (exit {proc.returncode}) — narrow path or glob."
            return found
        # -P needs git built with PCRE; anything else wrong with git: walk instead.
        if not (regex and syntax == ["-P"] and re.search(r"perl|pcre", err, re.I)):
            return None
    return None


def _read_git_grep(
    proc: subprocess.Popen, top: str, start: str, base: str, root: str, globs: list[str],
    mode: str, limit: int, policy: Any,
) -> tuple[_Found, bool]:
    found = _Found()
    allowed: dict[str, Optional[str]] = {}
    scope = os.path.normcase(os.path.normpath(start))

    def shown(path: str) -> Optional[str]:
        """The path to show for a git path, or None when the search must not report it."""
        if path not in allowed:
            full = os.path.normpath(os.path.join(top, path))
            folded = os.path.normcase(full)
            ok = (folded == scope or folded.startswith(scope.rstrip("\\/") + os.sep))
            ok = ok and not full.lower().endswith(_BINARY_SUFFIXES)
            ok = ok and (not globs or any(_path_matches(_relto(full, base), g) for g in globs))
            allowed[path] = _rel(full, root) if ok and ai_access_allowed(full, policy=policy) else None
        return allowed[path]

    read_any = False
    current: Optional[str] = None
    expect_heading = True
    assert proc.stdout is not None
    for raw in proc.stdout:
        read_any = True
        line = raw.decode("utf-8", errors="replace").rstrip("\n").rstrip("\r")
        if mode != "content":
            path, _, count = line.partition("\0")
            rel = shown(path)
            if rel is None or not count.isdigit():
                continue
            if mode == "files" and len(found.counts) >= limit:
                found.note = f"INCOMPLETE: stopped at {limit} files — narrow path or glob, or raise max_results."
                break
            found.hit(rel, int(count))
            continue
        if not line:
            expect_heading = True
            continue
        if expect_heading:
            current, expect_heading = shown(line), False
            continue
        if line == "--" or current is None:
            continue
        m = _GIT_ROW.match(line)
        if not m:
            continue
        is_match = m.group(2) == ":"
        if is_match:
            if sum(found.counts.values()) >= limit:
                found.note = f"INCOMPLETE: stopped at {limit} matches — narrow path or glob, or raise max_results."
                break
            found.hit(current)
        found.row(current, int(m.group(1)), m.group(3), is_match)
    return found, read_any


def _glob_escape(path: str) -> str:
    return re.sub(r"([*?\[])", r"[\1]", path)


def _git_pathspecs(top: str, srel: str, start: str, base: str, globs: list[str]) -> list[str]:
    """Pathspecs that cover what the globs can match inside ``srel``. The hits are matched
    against the globs again afterwards, so a broader pathspec is only slower, never wrong."""
    scope = [f":(literal){srel}" if srel else "."]
    if not globs or os.path.isfile(start):
        return scope
    base_rel = _relto(base, top)
    specs: list[str] = []
    for g in globs:
        pat = g.lstrip("/")
        if not any(ch in pat for ch in "*?["):
            return scope  # a plain word matches anywhere in the path
        if "/" not in pat:
            prefix = _glob_escape(srel) + "/" if srel else ""
            specs.append(f":(glob,icase){prefix}**/{pat}")
        elif os.path.isabs(base_rel) or base_rel.startswith(".."):
            return scope
        else:
            prefix = "" if base_rel in ("", ".") else _glob_escape(base_rel) + "/"
            specs.append(f":(glob,icase){prefix}{pat}")
    return specs


def _walk_search(
    start: str, base: str, root: str, globs: list[str], rx: re.Pattern[str], context: int,
    mode: str, limit: int, policy: Any, deadline: float,
) -> _Found:
    found = _Found()
    found.files_searched = 0
    try:
        _walk_search_files(found, _walk_files(start, base, globs, policy, deadline), root, rx, context, mode, limit,
                           policy, deadline)
    except _TimeUp:
        found.timed_out = True
        found.note = (f"INCOMPLETE: time limit {int(_SEARCH_SECONDS)} s, searched {found.files_searched} files "
                      "— narrow path or pattern.")
    return found


def _walk_search_files(
    found: _Found, walker: Any, root: str, rx: re.Pattern[str], context: int, mode: str, limit: int,
    policy: Any, deadline: float,
) -> None:
    for full in walker:
        if time.monotonic() > deadline:
            raise _TimeUp
        if not ai_access_allowed(full, policy=policy):
            continue
        text = _read_text(full, checked=True)
        if text is None:
            continue
        found.files_searched += 1
        if not rx.search(text):
            continue
        rel = _rel(full, root)
        if mode == "files" and len(found.counts) >= limit:
            found.note = f"INCOMPLETE: stopped at {limit} files — narrow path or glob, or raise max_results."
            break
        lines = text.splitlines()
        for i, line in enumerate(lines):
            if not rx.search(line):
                continue
            if mode == "content" and sum(found.counts.values()) >= limit:
                found.note = f"INCOMPLETE: stopped at {limit} matches — narrow path or glob, or raise max_results."
                break
            found.hit(rel)
            if mode != "content":
                continue
            for j in range(max(0, i - context), min(len(lines), i + context + 1)):
                found.row(rel, j + 1, lines[j], j == i)
        if found.note:
            break


def _time_limit(searched: Optional[int]) -> dict[str, Any]:
    """The fields a search or find adds when its time limit cut it short."""
    out: dict[str, Any] = {"truncated": True, "reason": f"time limit {int(_SEARCH_SECONDS)} s"}
    if searched is not None:
        out["searched_files"] = searched
    out["hint"] = "narrow path or pattern"
    return out


def _plural(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else ('es' if word.endswith('ch') else 's')}"


def _search_output(found: _Found, mode: str, context: int, fmt: str, pretty: bool, under: str = "") -> str:
    total, nfiles = sum(found.counts.values()), len(found.counts)
    if fmt == "json":
        out: dict[str, Any] = {"complete": not found.note}
        if found.note:
            out["note"] = found.note
        if found.files_searched is not None:
            out["files_searched"] = found.files_searched
        if found.timed_out:
            out.update(_time_limit(found.files_searched))
        if mode == "content":
            matches: list[dict[str, Any]] = []
            for path, rows in found.rows.items():
                for n in sorted(rows):
                    if not rows[n][1]:
                        continue
                    hit: dict[str, Any] = {"path": path, "line": n, "text": rows[n][0]}
                    if context:
                        hit["before"] = [rows[k][0] for k in range(n - context, n) if k in rows]
                        hit["after"] = [rows[k][0] for k in range(n + 1, n + 1 + context) if k in rows]
                    matches.append(hit)
            out["matches"] = matches
        elif mode == "files":
            out["files"] = [{"path": p, "hits": c} for p, c in found.counts.items()]
        out["total_hits"], out["files_with_hits"] = total, nfiles
        return tool_json(out, pretty=pretty)
    what = f"{_plural(total, 'match')} in {_plural(nfiles, 'file')}" if total else "no matches"
    searched = f" ({found.files_searched} files searched)" if found.files_searched is not None else ""
    head = f"{found.note} Found so far: {what}." if found.note else f"complete: {what}{searched}"
    if under:
        head += f" Paths are relative to {under}/"

    def show(path: str) -> str:
        return path[len(under) + 1:] if under and path.startswith(under + "/") else path

    lines = [head]
    if mode == "files":
        lines += [f"{show(p)}  {c}" for p, c in found.counts.items()]
    elif mode == "content":
        for path, rows in found.rows.items():
            prev = None
            for n in sorted(rows):
                if context and prev is not None and n != prev + 1:
                    lines.append("--")
                text, is_match = rows[n]
                lines.append(f"{show(path)}:{n}: {text}" if is_match else f"{show(path)}-{n}- {text}")
                prev = n
    return "\n".join(lines)


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
    format: str = "text",
    pretty: bool = False,
) -> str:
    """Search text in project files (git grep in a repo). Never searches .git, ignored, node_modules or binary files.

    pattern: the text to find (regex=true for a regular expression). path: a folder or file
    in the open project (default: all of it), or an absolute path in any of the person's
    other Ducky projects. glob: only these files, e.g. "*.py" or
    "src/**/*.ts" (comma-separated for several). context: lines shown before and after each hit.
    output_mode: "content" (matching lines), "files" (just the files, with hit counts) or
    "count" (totals) — "files" first is cheapest on a broad search.
    The first line says "complete: ..." or "INCOMPLETE: ..." (stopped early: narrow and search
    again before trusting a miss); outside the open project it also names the folder the
    paths are relative to. Then one "path:line: text" per hit ("path-line- text" for
    context lines). format="json" returns {complete, note, matches/files, total_hits}.
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
    policy = current_policy()
    policy.require(start)
    deadline = time.monotonic() + _SEARCH_SECONDS
    scope = _git_scope(start, policy)
    found = None
    if scope:
        found = _git_search(scope, start, base, root, globs, pattern, regex, case_sensitive, context, mode, limit,
                            policy, deadline)
    if found is None:
        found = _walk_search(start, base, root, globs, rx, context, mode, limit, policy, deadline)
    fmt = (format or "text").strip().lower()
    # Outside the open project every path is absolute: name the folder once, not on every line.
    shown = _rel(start, root)
    under = (shown if os.path.isdir(start) else os.path.dirname(shown).replace("\\", "/")) if os.path.isabs(shown) else ""
    text = _search_output(found, mode, context, fmt, pretty, under=under.rstrip("/"))
    return text if fmt == "json" else _inline_or_spill("workspace_search", text)


def _git_list(start: str, policy: Any, timeout: float = _SEARCH_SECONDS) -> Optional[list[str]]:
    """Tracked and untracked, not-ignored files under ``start`` (git ls-files), or None outside git."""
    scope = _git_scope(start, policy)
    if not scope:
        return None
    top, srel = scope
    proc = _git(["ls-files", "-co", "--exclude-standard", "-z", "--", f":(literal){srel}" if srel else "."], top,
                max(0.1, timeout))
    if proc is None or proc.returncode != 0:
        return None
    names = sorted({n for n in proc.stdout.decode("utf-8", errors="replace").split("\0") if n})
    return [os.path.normpath(os.path.join(top, n)) for n in names]


@mcp.tool()
def workspace_find(pattern: str = "*", path: str = ".", max_results: int = 200, pretty: bool = False) -> str:
    """Find files by name or path glob (git ls-files in a repo). Never lists .git, ignored or binary files.

    pattern: "*.py", "**/test_*.py", "src/**/mcp*", or a plain word (any path containing it).
    path: a folder in the open project (default: all of it), or an absolute path in any of the
    person's other Ducky projects. Returns paths relative to the open project, absolute elsewhere.
    "complete": false means it stopped early (see "note"): narrow it before trusting a miss.
    """
    root = _project_root()
    start = resolve_workspace_path(path or ".")
    base = _match_base(start, root)
    limit = max(1, min(int(max_results or 200), 2000))
    pat = pattern or "*"
    policy = current_policy()
    policy.require(start)
    deadline = time.monotonic() + _SEARCH_SECONDS
    found: list[str] = []
    note = ""
    seen: Optional[int] = None

    def take(full: str) -> bool:
        """Add ``full``; False once the limit is passed (so a full page really has more)."""
        nonlocal note
        if not ai_access_allowed(full, policy=policy):
            return True
        if len(found) >= limit:
            note = f"INCOMPLETE: stopped at {limit} files — narrow path or pattern, or raise max_results."
            return False
        found.append(_rel(full, root))
        return True

    timed_out = False
    listed = _git_list(start, policy, deadline - time.monotonic())
    try:
        if listed is not None:
            for full in listed:
                if time.monotonic() > deadline:
                    raise _TimeUp
                if (full.lower().endswith(_BINARY_SUFFIXES) or not _path_matches(_relto(full, base), pat)
                        or not os.path.isfile(full)):
                    continue
                if not take(full):
                    break
        else:
            seen = 0
            for full in _walk_files(start, base, [pat], policy, deadline):
                seen += 1
                if not take(full):
                    break
    except _TimeUp:
        timed_out = True
        note = f"INCOMPLETE: time limit {int(_SEARCH_SECONDS)} s — narrow path or pattern."
    out: dict[str, Any] = {"complete": not note}
    if note:
        out["note"] = note
    if timed_out:
        out.update(_time_limit(seen))
    out["files"], out["count"] = found, len(found)
    return tool_json(out, pretty=pretty)


# ----------------------------------------------------------------------------- edits


def _write_edit(
    relative_path: str, before: str, after: str, tool: str, extra: dict[str, Any], pretty: bool = False,
    *, create: bool = False,
) -> str:
    from backend.workspace.paths import content_hash
    from backend.workspace.runtime import get_writer

    # Edits work on the file as read (\n lines); a CRLF file is saved back as CRLF.
    if not create and _uses_crlf(relative_path):
        after = after.replace("\r\n", "\n").replace("\n", "\r\n")
    # create: the file must still be missing when the write lands (never overwrites).
    result = get_writer().write_text(
        relative_path, after, op="create" if create else "write",
        expected_hash=None if create else content_hash(before), tool=tool,
    )
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
            f"{label}old_text is required on a file that has text. To add text at the end, use "
            f"workspace_replace_lines with start_line={lines + 1}, end_line={lines}; to replace the whole "
            "file use workspace_write_file."
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
    """Replace exact text in a project file (find and replace). Never rewrites the whole file or edits via a shell.

    old_text must match the file exactly, spaces and line breaks included, and appear once
    (add surrounding lines to make it unique, or pass replace_all=true). An empty old_text
    creates the file with new_text, or fills it when it is empty. To add lines at the end of
    a file that has text, use workspace_replace_lines. Same write rules, history and change
    journal as workspace_write_file.
    """
    relative_path = path_arg(relative_path, path)
    if not old_text:
        # Agents told "add this line, creating the file if needed" send exactly this.
        full = resolve_workspace_path(relative_path)
        require_ai_access(full)
        if not os.path.exists(full):
            return _write_edit(relative_path, "", new_text, "workspace_edit_file", {"created": True}, pretty, create=True)
        if os.path.isfile(full) and _current_text(relative_path) == "":
            return _write_edit(relative_path, "", new_text, "workspace_edit_file", {"replacements": 1}, pretty)
    text = _current_text(relative_path)
    updated, count = _apply_edit(text, old_text, new_text, replace_all)
    return _write_edit(relative_path, text, updated, "workspace_edit_file", {"replacements": count}, pretty)


@mcp.tool()
def workspace_multi_edit(
    relative_path: str = "", *, edits: list[dict[str, Any]], pretty: bool = False, path: str = ""
) -> str:
    """Several exact-text replacements in one file, saved together. Never saves part of them: all or none.

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
        # Claude models write each edit the way their own Edit tool takes it (old_string /
        # new_string); Oct 22 2026 a Sonnet 5.5 member's multi-edit failed on that alone.
        old = edit.get("old_text", edit.get("old_string"))
        new = edit.get("new_text", edit.get("new_string"))
        updated, count = _apply_edit(updated, str(old or ""), str(new or ""),
                                     bool(edit.get("replace_all")), label=f"edit {n}: ")
        total += count
    return _write_edit(relative_path, text, updated, "workspace_multi_edit", {"edits": len(edits), "replacements": total}, pretty)


@mcp.tool()
def workspace_replace_lines(
    relative_path: str = "", *, start_line: int, end_line: int, new_text: str, pretty: bool = False, path: str = ""
) -> str:
    """Replace, insert or delete a line range in a project file. Never needs the old text repeated.

    Replaces lines start_line..end_line (1-based, inclusive) with new_text. To insert before
    line N, pass start_line=N and end_line=N-1
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
    """Move or rename a project file or folder (undoable). Never overwrites an existing destination.

    destination: the new project-relative path. With UEFN open, .verse files move through
    Epic's VerseToolset so the Verse build follows them (via: "uefn"). A Verse folder moved in a shell breaks every later build with
    Script error 3000, so use this.
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
    path, via = src, ""
    if dst_parent != src_parent:
        require_ai_path_operation(resolve_workspace_path(src), resolve_workspace_path(intermediate))
        out = project_files.move_project_entry(src, dst_parent)
        path, via = out["path"], out.get("via") or via
    if dst_name != src_name:
        out = project_files.rename_project_entry(path, dst_name)
        path, via = out["path"], out.get("via") or via
    return tool_json({"path": path, "from": src, **({"via": via} if via else {})}, pretty=pretty)


@mcp.tool()
def workspace_delete_file(relative_path: str = "", pretty: bool = False, path: str = "") -> str:
    """Delete a project file or folder into Ducky's undo trash. Never deletes for good: restorable from Changes.

    Prefer this to a shell rm / Remove-Item. With UEFN open, .verse files
    are deleted through Epic's VerseToolset so the Verse build drops them (via: "uefn").
    """
    from frontend.ui_web import project_files

    relative_path = path_arg(relative_path, path)

    from backend.workspace.ai_ignore import require_ai_path_operation
    require_ai_path_operation(resolve_workspace_path(relative_path))
    out = project_files.delete_project_entry(relative_path)
    payload = {"path": out.get("path"), "deleted": True, "restorable": True}
    if out.get("via"):
        payload["via"] = out["via"]
    return tool_json(payload, pretty=pretty)


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
def workspace_git(
    command: str, args: Optional[list[str]] = None, path: str = ".", format: str = "text", pretty: bool = False
) -> str:
    """Read-only git in the project (status, diff, log, show, blame, grep...). Never commits, pushes or changes the repo.

    command: status, diff, log, show, ls-files, blame, branch, grep, rev-parse, shortlog, describe, tag.
    path: where to run it, in the open project (default) or an absolute path in any of the
    person's other Ducky projects (that folder's repository).
    args: the rest of the command line as a list, e.g. command="log", args=["--oneline", "-20"]
    or command="diff", args=["HEAD", "--", "src/app.py"]. Returns git's own output; a first line
    "exit N" and git's error text only when it fails. format="json" returns
    {exit_code, output, stderr}. Commits, pushes and other changes stay in the shell, where they ask first.
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
    try:
        proc = subprocess.run(
            ["git", "-c", "core.quotepath=off", "--no-pager", cmd, *argv],
            cwd=cwd, env=_git_env(), stdin=subprocess.DEVNULL, capture_output=True, timeout=30,
            creationflags=_NO_WINDOW,
        )
    except FileNotFoundError as exc:
        raise ValueError("git is not installed on this PC (or not on PATH).") from exc
    except subprocess.TimeoutExpired as exc:
        raise ValueError(f"git {cmd} took over 30 s: narrow it (a path, -n, a range).") from exc
    out = proc.stdout.decode("utf-8", errors="replace")
    err = proc.stderr.decode("utf-8", errors="replace").strip()
    cut = f"Output cut at {_GIT_OUTPUT_CHARS} characters: narrow it (a path, -n, --stat)." if len(out) > _GIT_OUTPUT_CHARS else ""
    if (format or "text").strip().lower() == "json":
        payload: dict[str, Any] = {"exit_code": proc.returncode, "output": out[:_GIT_OUTPUT_CHARS]}
        if cut:
            payload["truncated"] = cut
        if err:
            payload["stderr"] = err[:4000]
        return tool_json(payload, pretty=pretty)
    text = out[:_GIT_OUTPUT_CHARS].rstrip("\n")
    if proc.returncode != 0:
        text = "\n".join(part for part in (f"exit {proc.returncode}", err[:4000], text) if part)
    elif not text:
        text = "(no output)"
    if cut:
        text += f"\n[{cut}]"
    return text
