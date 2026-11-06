"""Which tests a change reaches: an import graph of the repo's Python files (stdlib only).

The folder rule (run the test_*.py beside a changed file) misses a test in another folder
that imports it, and the bug comes back from there. build_graph maps every repo file to the
repo files it imports: top-level and in-function imports, relative imports, and
importlib.import_module / __import__ with a literal name. Names resolve the way the tests
run: ducky_app/ and the repo root are both import roots, and release/ scripts import each
other by bare name. Parsed imports are cached in .pytest_cache/impacted_graph.json by each
file's mtime and size; resolution always runs fresh, so a new or deleted file is seen.

impacted() turns that into the test files a change reaches through any chain of imports,
plus an always-run set, and asks for the whole suite when the graph can't see the change
(pytest config, requirements, the root conftest, or a module that loads code by path).
Run the tests for your change (from the repo root):
    py -3 ducky_app/impacted_tests.py --run                 # changes since origin/main + uncommitted
    py -3 ducky_app/impacted_tests.py --list --files a.py   # just print the selection
    py -3 ducky_app/impacted_tests.py --since HEAD~3 --run -- -x   # extra pytest args after --
"""

from __future__ import annotations

import argparse
import ast
import fnmatch
import json
import os
import subprocess
import sys
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

SCAN_DIRS = ("ducky_app", "tests", "release")
SKIP_DIRS = {"node_modules", ".venv", "dist", "build", "__pycache__", ".git"}
SKIP_PREFIXES = ("ducky_app/frontend/ui_web/web/",)
IMPORT_ROOTS = ("ducky_app", "")
CACHE_FILE = PurePosixPath(".pytest_cache/impacted_graph.json")
CACHE_VERSION = 2
ROOT_CONFTESTS = {"conftest.py", "ducky_app/conftest.py"}
RUN_ALL_PATTERNS = ("pytest.ini", "requirements*.txt")
ALWAYS_RUN = ("ducky_app/test_file_sizes.py",)
ALWAYS_RUN_NAME_PARTS = ("import_boundar", "settings_schema")

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


def parse_imports(source: bytes) -> tuple[list[Entry], bool]:
    """The file's imports, and whether it loads code the graph can't follow (by path, runpy,
    or import_module with a computed name)."""
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError):
        return [], False
    out: list[Entry] = []
    dynamic = False
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            out.extend([0, alias.name, []] for alias in node.names)
            dynamic |= any(alias.name == "runpy" for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            out.append([node.level, node.module or "", [a.name for a in node.names if a.name != "*"]])
            dynamic |= node.module == "runpy"
        elif isinstance(node, ast.Call):
            func = node.func
            name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
            if name == "spec_from_file_location":
                dynamic = True
            elif name in ("import_module", "__import__") and node.args:
                arg = node.args[0]
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                    literal = arg.value
                    level = len(literal) - len(literal.lstrip("."))
                    out.append([level, literal[level:], []])
                else:
                    dynamic = True
    return out, dynamic


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


def _load_cache(path: Path) -> dict:
    """{"files": {rel: [key, entries, dynamic]}, "graph": {rel: [deps]}} or {}."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict) or data.get("version") != CACHE_VERSION:
        return {}
    if not isinstance(data.get("files"), dict) or not isinstance(data.get("graph"), dict):
        return {}
    return data


def _save_cache(path: Path, files: dict[str, list], graph: dict[str, set[str]]) -> None:
    data = {"version": CACHE_VERSION, "files": files, "graph": {k: sorted(v) for k, v in graph.items()}}
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data), encoding="utf-8")
        os.replace(tmp, path)
    except OSError:
        pass  # a read-only checkout just parses every time


@dataclass
class Graph:
    imports: dict[str, set[str]]  # file -> repo files it imports directly
    dynamic: set[str] = field(default_factory=set)  # files that load code by path or computed name


def _build(root: Path, use_cache: bool) -> Graph:
    files = py_files(root)
    cache_path = root / CACHE_FILE
    cached = _load_cache(cache_path).get("files", {}) if use_cache else {}
    parsed: dict[str, list] = {}
    for rel in files:
        st = (root / rel).stat()
        key = [st.st_mtime_ns, st.st_size]
        hit = cached.get(rel)
        if isinstance(hit, list) and len(hit) == 3 and hit[0] == key:
            parsed[rel] = hit
        else:
            parsed[rel] = [key, *parse_imports((root / rel).read_bytes())]
    index = _Index(files)
    graph = Graph({})
    for rel, (_key, entries, dynamic) in parsed.items():
        deps: set[str] = set()
        for entry in entries:
            deps |= index.resolve(rel, entry)
        graph.imports[rel] = deps
        if dynamic:
            graph.dynamic.add(rel)
    if use_cache:
        _save_cache(cache_path, parsed, graph.imports)
    return graph


def build_graph(root: str | os.PathLike[str], use_cache: bool = True) -> dict[str, set[str]]:
    """Repo-relative file -> the repo files it imports (directly)."""
    return _build(Path(root), use_cache).imports


def is_test_file(rel: str) -> bool:
    name = PurePosixPath(rel).name
    return name.endswith(".py") and (name.startswith("test_") or name.endswith("_test.py"))


@dataclass
class Selection:
    tests: list[str]
    run_all: bool = False
    reason: str = ""


def _normalize(root: Path, path: str, known: set[str]) -> str:
    p = Path(path)
    if p.is_absolute():
        try:
            p = p.resolve().relative_to(root.resolve())
        except ValueError:
            pass
    rel = PurePosixPath(p.as_posix()).as_posix()  # drops a leading ./
    # `frontend/...` and `backend/...` are written relative to ducky_app/ as often as not.
    if rel not in known and not (root / rel).exists():
        if f"ducky_app/{rel}" in known or (root / "ducky_app" / rel).exists():
            return f"ducky_app/{rel}"
    return rel


def impacted(root: str | os.PathLike[str], changed_paths: list[str], use_cache: bool = True) -> Selection:
    """Every test file a change reaches through imports, plus the always-run checks; or the
    whole suite (run_all) when the change is something the import graph can't see."""
    root = Path(root)
    before = _load_cache(root / CACHE_FILE).get("graph", {}) if use_cache else {}
    graph = _build(root, use_cache)
    all_tests = sorted(f for f in graph.imports if is_test_file(f))
    known = set(graph.imports) | set(before)
    changed = {_normalize(root, p, known) for p in changed_paths}

    def everything(reason: str) -> Selection:
        return Selection(all_tests, True, reason)

    for rel in sorted(changed):
        if rel in ROOT_CONFTESTS:
            return everything(f"{rel} is the root conftest")
        if any(fnmatch.fnmatch(PurePosixPath(rel).name, pat) for pat in RUN_ALL_PATTERNS) and "/" not in rel:
            return everything(f"{rel} changes how every test runs")
        if rel in graph.dynamic:
            return everything(f"{rel} loads code by path, which the import graph can't follow")

    reverse: dict[str, set[str]] = {}
    for importer, deps in graph.imports.items():
        for dep in deps:
            reverse.setdefault(dep, set()).add(importer)
    # A deleted or renamed file: whoever imported it before still depends on the change.
    for rel in changed - set(graph.imports):
        if not rel.endswith(".py"):
            continue
        if rel not in before and not any(rel in deps for deps in before.values()):
            return everything(f"{rel} is gone and no cached graph says who imported it")
        for importer, deps in before.items():
            if rel in deps:
                reverse.setdefault(rel, set()).add(importer)

    reached: set[str] = set()
    queue = deque(changed)
    while queue:  # visited set, so import cycles end
        rel = queue.popleft()
        if rel in reached:
            continue
        reached.add(rel)
        queue.extend(reverse.get(rel, ()))
    selected = {f for f in reached if is_test_file(f) and f in graph.imports}
    for rel in changed:
        if PurePosixPath(rel).name == "conftest.py":
            folder = f"{PurePosixPath(rel).parent.as_posix()}/"
            selected |= {t for t in all_tests if t.startswith(folder)}
    selected |= {t for t in ALWAYS_RUN if t in graph.imports}
    selected |= {t for t in all_tests if any(part in PurePosixPath(t).name for part in ALWAYS_RUN_NAME_PARTS)}
    return Selection(sorted(selected), False, f"{len(changed)} changed file(s) reach {len(selected)} test file(s)")


REPO_ROOT = Path(__file__).resolve().parent.parent
FULL_SUITE = ("ducky_app", "tests", "release")
WEB_PANEL = "ducky_app/frontend/ui_web/web/"
MAX_CMD_CHARS = 24_000  # Windows caps a command line at 32,767 characters


def _git(root: Path, *args: str) -> list[str]:
    done = subprocess.run(["git", *args], cwd=root, capture_output=True, text=True, check=True)
    return [line.strip() for line in done.stdout.splitlines() if line.strip()]


def changed_files(root: Path, since: str) -> list[str]:
    """Committed since `since` (merge base), uncommitted, and untracked .py files."""
    out = set(_git(root, "diff", "--name-only", f"{since}...HEAD"))
    out |= set(_git(root, "diff", "--name-only", "HEAD"))
    out |= {p for p in _git(root, "ls-files", "--others", "--exclude-standard") if p.endswith(".py")}
    return sorted(out)


def _pytest(runner, root: Path, targets: list[str], extra: list[str]) -> int:
    # ponytail: chunked so a big selection fits a Windows command line; first failure's code wins.
    base = [sys.executable, "-m", "pytest", "-q", *extra]
    chunks: list[list[str]] = [[]]
    for target in targets:
        if chunks[-1] and sum(len(t) + 1 for t in chunks[-1]) + len(target) > MAX_CMD_CHARS:
            chunks.append([])
        chunks[-1].append(target)
    code = 0
    for chunk in chunks:
        rc = runner([*base, *chunk], root)
        code = code or rc
    return code


def _call(cmd: list[str], cwd: Path) -> int:
    return subprocess.call(cmd, cwd=cwd)


def main(argv: list[str] | None = None, root: Path = REPO_ROOT, runner=_call) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    extra: list[str] = []
    if "--" in argv:
        at = argv.index("--")
        argv, extra = argv[:at], argv[at + 1:]
    parser = argparse.ArgumentParser(prog="impacted_tests.py", description="Run the tests a change reaches.")
    parser.add_argument("--since", default="origin/main", help="git ref to diff against (default origin/main)")
    parser.add_argument("--files", nargs="+", help="changed files, instead of asking git")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--list", action="store_true", help="print the selected test files (default)")
    mode.add_argument("--run", action="store_true", help="run them with pytest")
    args = parser.parse_args(argv)

    if args.files:
        changed, source = list(args.files), "--files"
    else:
        try:
            changed = changed_files(root, args.since)
        except (OSError, subprocess.CalledProcessError) as exc:
            print(f"git could not list changes since {args.since}: {getattr(exc, 'stderr', '') or exc}", file=sys.stderr)
            return 2
        source = f"since {args.since}"
    if any(Path(p).as_posix().startswith(WEB_PANEL) for p in changed):
        print("web panel changed: also run vitest")
    if not changed:
        print(f"no changed files ({source}): no tests selected")
        return 0

    sel = impacted(root, changed)
    if sel.run_all:
        print(f"run all: {sel.reason}")
        if args.run:
            return _pytest(runner, root, list(FULL_SUITE), extra)
        print("\n".join(sel.tests))
        return 0
    if not sel.tests:
        print(f"no test files for {len(changed)} changed files ({source})")
        return 0
    print(f"{len(sel.tests)} test files for {len(changed)} changed files ({source})")
    if args.run:
        return _pytest(runner, root, sel.tests, extra)
    print("\n".join(sel.tests))
    return 0


if __name__ == "__main__":
    sys.exit(main())
