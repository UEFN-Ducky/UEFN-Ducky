"""Architecture fitness test: project files are written only by the pipeline.

Scans the modules that touch the user's UEFN project for raw filesystem
mutations (``open(..., "w")``, ``Path.write_text``, ``os.replace``,
``shutil.move`` ...) and fails when one appears outside ``writer.py`` and the
explicit allowlist below. Add to the allowlist only for writes that target
AppData or OS temp, or for a ``perform`` callback handed to
``ProjectWriter.path_op`` (the filesystem step of a rename/move/copy/delete
that the pipeline runs under its own locks, policy and journal).
"""

from __future__ import annotations

import ast
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[2]  # ducky_app/

SCAN_GLOBS = (
    "backend/tools/**/*.py",
    "backend/testing/**/*.py",
    "backend/workspace/**/*.py",
    "frontend/ui_web/project_files.py",
    "frontend/ui_web/panel_api_project.py",
    "frontend/ui_web/verse_editor/io.py",
)

# The one sanctioned writer.
SANCTIONED = {"backend/workspace/writer.py"}

# path::function -> reason. Keep reasons honest; a reviewer reads them.
ALLOWLIST: dict[str, str] = {
    # AppData / temp, not the project (permanent)
    "backend/tools/panel/panel_ai_plugins.py::_write_json": "plugin draft under AppData",
    "backend/tools/panel/panel_ai_plugins.py::scaffold_ai_plugin": "plugin draft under AppData",
    "backend/tools/panel/panel_ai_plugins.py::write_ai_plugin_file": "plugin draft under AppData",
    "backend/tools/panel/panel_ai_plugins.py::delete_ai_plugin_draft": "plugin draft under AppData",
    "backend/tools/panel/panel_ai_plugins.py::_self_check": "self-check temp dir",
    "backend/tools/panel/panel_verse_templates.py::_self_check": "self-check temp dir",
    "backend/tools/verse/verse_stats.py::_append": "stats ledger under AppData",
    "frontend/ui_web/project_files.py::write_external_file": "ext: files live outside the project",
    "frontend/ui_web/project_files.py::purge_undo_trash": "undo-trash housekeeping, not project content",
    # `perform` callbacks: the filesystem step of ProjectWriter.path_op, run under its
    # locks, policy and journal. The enclosing function passes them to _pipeline_path_op.
    "frontend/ui_web/project_files.py::rename_project_entry._do_rename": "path_op perform",
    "frontend/ui_web/project_files.py::move_project_entry._do_move": "path_op perform",
    "frontend/ui_web/project_files.py::copy_project_entry._do_copy": "path_op perform",
    "frontend/ui_web/project_files.py::import_external_entries._do_import": "path_op perform",
    "frontend/ui_web/project_files.py::delete_project_entry._do_trash": "path_op perform",
    "frontend/ui_web/project_files.py::restore_trashed_entry._do_restore": "path_op perform",
    "backend/tools/verse/template_verify.py::_cleanup._unlink": "path_op perform",
    "backend/workspace/journal.py::_unlink_through_pipeline._unlink": "path_op perform",
    "backend/workspace/journal.py::_move_back_through_pipeline._move": "path_op perform",
    # The journal writes its own ledger under AppData and restores through the
    # ProjectWriter instance it is handed (a Name receiver the scanner cannot see through).
    "backend/workspace/journal.py::_write_json": "journal run/index documents under AppData",
    "backend/workspace/journal.py::_store_blob": "journal blobs under AppData",
    "backend/workspace/human_watch.py::_save_saved": "human-watch fingerprint under AppData/changesets",
    "backend/workspace/journal.py::prune": "journal retention under AppData",
    "backend/workspace/journal.py::delete_runs": "journal Clear removes run documents under AppData",
    "backend/workspace/journal.py::delete_entries": "journal row delete removes an emptied run document under AppData",
    "backend/workspace/journal.py::_sweep_orphan_blobs": "journal blob GC under AppData",
    "backend/workspace/journal.py::_revert_path": "restores via the ProjectWriter instance",
    # KNOWN DEBT — tester harness resolves its own root and writes directly. Tracked as
    # the follow-up task "Fix tester harness root for Verse/DuckyTests"; remove when landed.
    "backend/testing/verse_harness.py::add_verse_test_case": "DEBT tester harness",
    "backend/testing/verse_harness.py::save_simulation_scenario": "DEBT tester harness (.ducky/tests)",
    "frontend/ui_web/panel_api_project.py::tester_scaffold": "DEBT tester harness",
}

_SHUTIL_WRITES = {"move", "copy", "copy2", "copyfile", "copytree", "rmtree"}
_PATH_WRITES = {"write_text", "write_bytes", "rename", "replace", "unlink", "touch"}
_OS_WRITES = {"replace", "rename", "remove", "unlink"}


def _mode_writes(node: ast.Call, mode_index: int) -> bool:
    mode = None
    if len(node.args) > mode_index and isinstance(node.args[mode_index], ast.Constant):
        mode = node.args[mode_index].value
    for kw in node.keywords:
        if kw.arg == "mode" and isinstance(kw.value, ast.Constant):
            mode = kw.value.value
    return isinstance(mode, str) and any(ch in mode for ch in "wax+")


# Calls that return the ProjectWriter; `<accessor>().write_text(...)` is the pipeline.
PIPELINE_ACCESSORS = {"get_writer", "_pipeline", "_writer"}


def _receiver_is_pipeline(owner: ast.expr) -> bool:
    return (
        isinstance(owner, ast.Call)
        and isinstance(owner.func, ast.Name)
        and owner.func.id in PIPELINE_ACCESSORS
    )


def _is_write_call(node: ast.Call) -> bool:
    func = node.func
    if isinstance(func, ast.Name):
        return func.id == "open" and _mode_writes(node, 1)
    if not isinstance(func, ast.Attribute):
        return False
    owner = func.value
    if _receiver_is_pipeline(owner):
        return False
    owner_name = owner.id if isinstance(owner, ast.Name) else ""
    if owner_name == "os":
        if func.attr == "fdopen":
            return _mode_writes(node, 1)
        return func.attr in _OS_WRITES
    if owner_name == "shutil":
        return func.attr in _SHUTIL_WRITES
    if func.attr in {"open"} and _mode_writes(node, 0):
        return True  # Path.open("w")
    # Path-style writes on any receiver except plain strings (`str.replace` is not a write).
    if func.attr == "replace":
        return False
    return func.attr in _PATH_WRITES


class _Finder(ast.NodeVisitor):
    def __init__(self) -> None:
        self.stack: list[str] = []
        self.hits: list[tuple[str, int]] = []

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:  # noqa: N802
        self.stack.append(node.name)
        self.generic_visit(node)
        self.stack.pop()

    visit_AsyncFunctionDef = visit_FunctionDef  # noqa: N815

    def visit_Call(self, node: ast.Call) -> None:  # noqa: N802
        if _is_write_call(node):
            self.hits.append((".".join(self.stack) if self.stack else "<module>", node.lineno))
        self.generic_visit(node)


def _scan() -> dict[str, list[int]]:
    found: dict[str, list[int]] = {}
    for pattern in SCAN_GLOBS:
        for path in sorted(APP_ROOT.glob(pattern)):
            rel = path.relative_to(APP_ROOT).as_posix()
            if rel in SANCTIONED or path.name.startswith("test_"):
                continue
            finder = _Finder()
            finder.visit(ast.parse(path.read_text(encoding="utf-8"), filename=rel))
            for func, line in finder.hits:
                found.setdefault(f"{rel}::{func}", []).append(line)
    return found


def test_project_files_are_written_only_by_the_pipeline() -> None:
    found = _scan()
    unexpected = {k: v for k, v in found.items() if k not in ALLOWLIST}
    assert not unexpected, (
        "Raw filesystem writes outside backend/workspace/writer.py. Route them through "
        "ProjectWriter or, if they target AppData/temp, add them to ALLOWLIST with a reason:\n"
        + "\n".join(f"  {k} (lines {v})" for k, v in sorted(unexpected.items()))
    )


def test_scanner_recognises_pipeline_calls_and_raw_writes() -> None:
    def hits(src: str) -> list[str]:
        finder = _Finder()
        finder.visit(ast.parse(src))
        return [f for f, _ in finder.hits]

    assert hits('def f():\n    get_writer().write_text("a", "b")\n') == []
    assert hits('def f():\n    _pipeline().create("a", "b")\n') == []
    assert hits('def f():\n    target.write_text("b")\n') == ["f"]
    assert hits('def f():\n    with open(p, "w") as fh:\n        fh.write("x")\n') == ["f"]
    assert hits('def f():\n    with open(p) as fh:\n        fh.read()\n') == []
    assert hits('def f():\n    os.replace(a, b)\n') == ["f"]
    assert hits('def f():\n    s = s.replace("a", "b")\n') == []
    assert hits('def f():\n    d.mkdir()\n    d.rmdir()\n') == []
    assert hits('def outer():\n    def _do():\n        shutil.move(a, b)\n') == ["outer._do"]


def test_allowlist_has_no_stale_entries() -> None:
    found = _scan()
    stale = sorted(k for k in ALLOWLIST if k not in found)
    assert not stale, f"ALLOWLIST entries no longer write anything; remove them: {stale}"
