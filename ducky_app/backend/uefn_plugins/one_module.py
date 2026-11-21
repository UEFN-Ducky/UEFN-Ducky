"""A module written across several files, compiled as the one module it is.

A very long module can be cut into files that all run in the module's own namespace. From
source the module runs them itself, in order::

    _PARTS = ("_kit.py", "_roads.py")  # ducky-build: one module
    for _part in _PARTS:
        _path = os.path.join(os.path.dirname(__file__), _part)
        with open(_path, encoding="utf-8") as _fh:
            exec(compile(_fh.read(), _path, "exec", dont_inherit=True), globals())

so every name stays the module's own. A compiled build has no ``.py`` files to read: the build
engine (:func:`join`) puts the listed files' source where the marked line is, leaves the tuple
empty (the loop then runs nothing) and drops the files. One compiled module, the same names.

The marked line is one top-level ``NAME = ("a.py", "b.py")``: names of files beside the module,
in the order they run. Each part starts with the same ``from __future__`` lines as the module,
because the compiled file has one set of them.

Standard library only: the plugin check (:mod:`plugin_lint`) and the build engine both use it.
"""

from __future__ import annotations

import ast
import io
import tokenize
from pathlib import Path

MARK = "ducky-build: one module"
_FORM = (
    f'"# {MARK}" goes on one top-level line of the form NAME = ("a.py", "b.py"): the files that are '
    "part of this module, in the order they run."
)


class OneModuleError(ValueError):
    """The marked module cannot be built as written; the message says what to change."""


def _marked_lines(text: str) -> list[int]:
    """Lines whose comment carries the mark (the same words inside a string are not a mark)."""
    try:
        return [
            tok.start[0]
            for tok in tokenize.generate_tokens(io.StringIO(text).readline)
            if tok.type == tokenize.COMMENT and MARK in tok.string
        ]
    except (tokenize.TokenError, SyntaxError) as exc:
        raise OneModuleError(f"does not parse ({exc}).") from exc


def _parse(text: str, name: str) -> ast.Module:
    try:
        return ast.parse(text)
    except SyntaxError as exc:
        raise OneModuleError(f"{name} does not parse ({exc}).") from exc


def _future(tree: ast.Module) -> tuple[frozenset[str], list[ast.stmt]]:
    """The module's ``from __future__`` features, and the statements that ask for them."""
    names: set[str] = set()
    nodes: list[ast.stmt] = []
    for node in tree.body:
        if isinstance(node, ast.ImportFrom) and node.module == "__future__" and not node.level:
            names.update(alias.name for alias in node.names)
            nodes.append(node)
    return frozenset(names), nodes


def _alone(tree: ast.Module, stmt: ast.stmt) -> bool:
    """No other top-level statement shares a line with ``stmt``."""
    first, last = stmt.lineno, stmt.end_lineno or stmt.lineno
    return not any(
        other is not stmt and other.lineno <= last and (other.end_lineno or other.lineno) >= first
        for other in tree.body
    )


def declared(text: str) -> tuple[ast.Module, ast.Assign, list[str]] | None:
    """A module's marked line: (the module's tree, the assignment, the file names).

    None when the source carries no mark. Raises :class:`OneModuleError` when the mark is there
    but not on a line of the right form.
    """
    if MARK not in text:
        return None
    marks = _marked_lines(text)
    if not marks:
        return None
    if len(marks) > 1:
        raise OneModuleError(f"the mark is there {len(marks)} times. {_FORM}")
    tree = _parse(text, "it")
    stmt = next(
        (n for n in tree.body
         if isinstance(n, ast.Assign) and n.lineno <= marks[0] <= (n.end_lineno or n.lineno)),
        None,
    )
    if (
        stmt is None
        or len(stmt.targets) != 1
        or not isinstance(stmt.targets[0], ast.Name)
        or not isinstance(stmt.value, (ast.Tuple, ast.List))
        or not stmt.value.elts
        or not all(isinstance(e, ast.Constant) and isinstance(e.value, str) for e in stmt.value.elts)
        or not _alone(tree, stmt)
    ):
        raise OneModuleError(_FORM)
    return tree, stmt, [e.value for e in stmt.value.elts]  # type: ignore[attr-defined]


def _part(path: Path, host: str, future: frozenset[str]) -> str:
    """One part's source as it reads inside the module: the same statements, without its own
    ``from __future__`` lines (the module's cover the whole file), and a docstring kept as what
    running the file would have made of it (the module's ``__doc__``)."""
    try:
        text = path.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeDecodeError) as exc:
        raise OneModuleError(f"{path.name} cannot be read ({exc}).") from exc
    if MARK in text and _marked_lines(text):
        raise OneModuleError(f"{path.name} has parts of its own: list every file in {host} instead.")
    tree = _parse(text, path.name)
    mine, asks = _future(tree)
    if mine != future:
        raise OneModuleError(
            f"{path.name} and {host} must start with the same `from __future__` lines, because they are "
            f"compiled as one file: {host} has {', '.join(sorted(future)) or 'none'}, "
            f"{path.name} has {', '.join(sorted(mine)) or 'none'}."
        )
    lines = text.splitlines()
    for node in asks:
        if not _alone(tree, node):
            raise OneModuleError(f"{path.name}:{node.lineno}: put the `from __future__` import on a line of its own.")
        for at in range(node.lineno - 1, node.end_lineno or node.lineno):
            lines[at] = ""
    first = tree.body[0] if tree.body else None
    if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant) and isinstance(first.value.value, str):
        lines[first.lineno - 1] = "__doc__ = " + lines[first.lineno - 1]
    return "\n".join(lines) + "\n"


def joined(host: Path) -> tuple[str, list[str]] | None:
    """The source of a marked module with its parts in it, and the parts' file names.

    None for a module with no mark (or one that cannot be read). Raises :class:`OneModuleError`.
    """
    try:
        text = host.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeDecodeError):
        return None
    found = declared(text)
    if found is None:
        return None
    tree, stmt, names = found
    future, _asks = _future(tree)
    body: list[str] = []
    for at, name in enumerate(names):
        if name != Path(name).name or not name.endswith(".py") or name == host.name:
            raise OneModuleError(f"{name!r} must be the name of a .py file beside {host.name} (no folder).")
        if name in names[:at]:
            raise OneModuleError(f"{name} is listed twice.")
        if not (host.parent / name).is_file():
            raise OneModuleError(f"{name} is not beside {host.name}.")
        body.append(f"# ---- {name}\n" + _part(host.parent / name, host.name, future))
    lines = text.splitlines()
    target = stmt.targets[0].id  # type: ignore[attr-defined]
    head = lines[: stmt.lineno - 1] + [f"{target} = ()  # (the build put these files' source right below)"]
    tail = lines[stmt.end_lineno or stmt.lineno:]
    return "\n".join(head) + "\n" + "".join(body) + "\n".join(tail) + "\n", names


def join(package: Path) -> list[str]:
    """Join every marked module under ``package``, in place, and delete the files that became
    part of one. Returns those files (paths inside the package, ``/`` between folders)."""
    taken: list[str] = []
    for host in sorted(package.rglob("*.py")):
        if not host.is_file():
            continue  # a part an earlier module took
        rel = host.relative_to(package).as_posix()
        try:
            made = joined(host)
        except OneModuleError as exc:
            raise OneModuleError(f"{rel}: {exc}") from None
        if made is None:
            continue
        text, names = made
        host.write_text(text, encoding="utf-8", newline="\n")
        for name in names:
            (host.parent / name).unlink()
            taken.append((host.parent / name).relative_to(package).as_posix())
    return taken


def problems(package: Path, prefix: str = "") -> list[str]:
    """What stops the marked modules under ``package`` from being built, one line each. Changes nothing."""
    out: list[str] = []
    for host in sorted(package.rglob("*.py")):
        try:
            joined(host)
        except OneModuleError as exc:
            out.append(f"{prefix}{host.relative_to(package).as_posix()}: {exc}")
    return out
