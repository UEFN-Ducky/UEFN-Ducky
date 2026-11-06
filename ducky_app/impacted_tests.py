"""Which tests a change reaches: an import graph of the repo's Python files (stdlib only).

The folder rule (run the test_*.py beside a changed file) misses a test in another folder
that imports it, and the bug comes back from there. build_graph maps every repo file to the
repo files it imports: top-level and in-function imports, relative imports, and
importlib.import_module / __import__ with a literal name. Names resolve the way the tests
run: ducky_app/ and the repo root are both import roots, and release/ scripts import each
other by bare name. Parsed imports are cached in .pytest_cache/impacted_graph.json by each
file's mtime and size; resolution always runs fresh, so a new or deleted file is seen.
"""

from __future__ import annotations

import ast
import json
import os
from pathlib import Path, PurePosixPath

SCAN_DIRS = ("ducky_app", "tests", "release")
SKIP_DIRS = {"node_modules", ".venv", "dist", "build", "__pycache__", ".git"}
SKIP_PREFIXES = ("ducky_app/frontend/ui_web/web/",)
IMPORT_ROOTS = ("ducky_app", "")
CACHE_FILE = PurePosixPath(".pytest_cache/impacted_graph.json")
CACHE_VERSION = 1

# One parsed import: [level, module, names]. level 0 = absolute; names = the `from` names.
Entry = list


def py_files(root: Path) -> list[str]:
    """Repo-relative POSIX paths of every scanned .py file."""
    out: list[str] = []
    for top in SCAN_DIRS:
        base = root / top
        if not base.is_dir():
            continue
        for dirpath, dirnames, filenames in os.walk(base):
            rel_dir = Path(dirpath).relative_to(root).as_posix()
            dirnames[:] = [
                d for d in dirnames if d not in SKIP_DIRS and not f"{rel_dir}/{d}/".startswith(SKIP_PREFIXES)
            ]
            out.extend(f"{rel_dir}/{name}" for name in filenames if name.endswith(".py"))
    return sorted(out)


def parse_imports(source: bytes) -> list[Entry]:
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError):
        return []
    out: list[Entry] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            out.extend([0, alias.name, []] for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            out.append([node.level, node.module or "", [a.name for a in node.names if a.name != "*"]])
        elif isinstance(node, ast.Call) and node.args:
            arg, func = node.args[0], node.func
            name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
            if name in ("import_module", "__import__") and isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                literal = arg.value
                level = len(literal) - len(literal.lstrip("."))
                out.append([level, literal[level:], []])
    return out


class _Index:
    def __init__(self, files: list[str]) -> None:
        self.files = set(files)
        self.dirs: set[str] = set()
        for f in files:
            parent = PurePosixPath(f).parent
            while str(parent) != ".":
                self.dirs.add(parent.as_posix())
                parent = parent.parent

    def module(self, base: str, parts: list[str]) -> set[str] | None:
        """Files run by importing `parts` under `base`: package __init__s on the way and the
        module itself. None when no such module or package exists there."""
        prefix = f"{base}/" if base else ""
        if not parts:
            init = f"{base}/__init__.py"
            return {init} & self.files if base in self.dirs else None
        hits = {f"{prefix}{'/'.join(parts[:i])}/__init__.py" for i in range(1, len(parts))} & self.files
        path = prefix + "/".join(parts)
        if f"{path}.py" in self.files:
            hits.add(f"{path}.py")
        elif f"{path}/__init__.py" in self.files:
            hits.add(f"{path}/__init__.py")
        elif path not in self.dirs:  # not even a namespace package
            return None
        return hits

    def resolve(self, rel: str, entry: Entry) -> set[str]:
        level, module, names = entry
        here = PurePosixPath(rel).parent
        if level:
            base = here
            for _ in range(level - 1):
                base = base.parent
            bases = ["" if str(base) == "." else base.as_posix()]
        else:
            bases = list(IMPORT_ROOTS)
            if rel.startswith("release/"):
                bases.append(here.as_posix())
        parts = module.split(".") if module else []
        found: set[str] = set()
        for base in bases:
            hit = self.module(base, parts)
            if hit is None:
                continue
            found |= hit
            for name in names:  # `from pkg import submodule`
                found |= self.module(base, parts + [name]) or set()
        found.discard(rel)
        return found


def _load_cache(path: Path) -> dict[str, list]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict) or data.get("version") != CACHE_VERSION:
        return {}
    files = data.get("files")
    return files if isinstance(files, dict) else {}


def _save_cache(path: Path, files: dict[str, list]) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"version": CACHE_VERSION, "files": files}), encoding="utf-8")
        os.replace(tmp, path)
    except OSError:
        pass  # a read-only checkout just parses every time


def build_graph(root: str | os.PathLike[str], use_cache: bool = True) -> dict[str, set[str]]:
    """Repo-relative file -> the repo files it imports (directly)."""
    root = Path(root)
    files = py_files(root)
    cache_path = root / CACHE_FILE
    cached = _load_cache(cache_path) if use_cache else {}
    parsed: dict[str, list] = {}
    for rel in files:
        st = (root / rel).stat()
        key = [st.st_mtime_ns, st.st_size]
        hit = cached.get(rel)
        if isinstance(hit, list) and len(hit) == 2 and hit[0] == key:
            parsed[rel] = hit
        else:
            parsed[rel] = [key, parse_imports((root / rel).read_bytes())]
    if use_cache and parsed != cached:
        _save_cache(cache_path, parsed)
    index = _Index(files)
    graph: dict[str, set[str]] = {}
    for rel, (_key, entries) in parsed.items():
        deps: set[str] = set()
        for entry in entries:
            deps |= index.resolve(rel, entry)
        graph[rel] = deps
    return graph
