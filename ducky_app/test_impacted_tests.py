"""The import graph finds absolute, relative, in-function, submodule and importlib imports
across both import roots, and a cached graph matches a fresh build after an edit. impacted()
selects every test a change reaches through imports (where the folder rule missed some),
the always-run checks, and the whole suite for changes the graph can't see."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from impacted_tests import build_graph, impacted


def _write(root: Path, files: dict[str, str]) -> None:
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")


def _repo(tmp_path: Path) -> Path:
    _write(tmp_path, {
        "ducky_app/frontend/__init__.py": "",
        "ducky_app/frontend/ui_web/__init__.py": "",
        "ducky_app/frontend/ui_web/terminal/__init__.py": "from .manager import TerminalManager\n",
        "ducky_app/frontend/ui_web/terminal/manager.py": "import json\nclass TerminalManager: ...\n",
        "ducky_app/frontend/ui_web/web/skip.py": "import frontend.ui_web.terminal.manager\n",
        "ducky_app/backend/__init__.py": "",
        "ducky_app/backend/tools/__init__.py": "",
        "ducky_app/backend/tools/panel/__init__.py": "",
        "ducky_app/backend/tools/panel/tools.py": "from ..helpers import shared\n",
        "ducky_app/backend/tools/helpers.py": "shared = 1\n",
        "ducky_app/backend/tools/panel/test_fwd.py": (
            "import pytest\n"
            "from frontend.ui_web import terminal\n"
            "from . import tools\n"
            "def test_x():\n"
            "    from frontend.ui_web.terminal import manager\n"
        ),
        "ducky_app/backend/lazy.py": (
            "import importlib\n"
            "def load():\n"
            "    return importlib.import_module('backend.tools.helpers'), __import__('frontend.ui_web')\n"
        ),
        "ducky_app/node_modules/x.py": "import backend\n",
        "tests/test_root.py": "import ducky_app.backend.tools.helpers\nimport os, requests\n",
        "release/retry.py": "import publish\n",
        "release/publish.py": "",
    })
    return tmp_path


def test_every_import_form_resolves_to_repo_files(tmp_path: Path) -> None:
    graph = build_graph(_repo(tmp_path), use_cache=False)
    fwd = graph["ducky_app/backend/tools/panel/test_fwd.py"]
    assert "ducky_app/frontend/ui_web/terminal/manager.py" in fwd  # in-function import
    assert "ducky_app/frontend/ui_web/terminal/__init__.py" in fwd  # from pkg import submodule
    assert "ducky_app/frontend/__init__.py" in fwd  # parent packages run too
    assert "ducky_app/backend/tools/panel/tools.py" in fwd  # from . import x
    assert graph["ducky_app/backend/tools/panel/tools.py"] == {"ducky_app/backend/tools/helpers.py"}  # from ..y import z
    assert graph["ducky_app/frontend/ui_web/terminal/__init__.py"] == {"ducky_app/frontend/ui_web/terminal/manager.py"}
    lazy = graph["ducky_app/backend/lazy.py"]
    assert {"ducky_app/backend/tools/helpers.py", "ducky_app/frontend/ui_web/__init__.py"} <= lazy
    # The repo root is an import root too; stdlib and third-party names are ignored.
    assert "ducky_app/backend/tools/helpers.py" in graph["tests/test_root.py"]
    assert all(dep.endswith(".py") and not dep.startswith(("requests", "os")) for dep in graph["tests/test_root.py"])
    assert graph["release/retry.py"] == {"release/publish.py"}  # bare-name release scripts
    assert graph["ducky_app/frontend/ui_web/terminal/manager.py"] == set()


def test_skipped_folders_are_not_scanned(tmp_path: Path) -> None:
    graph = build_graph(_repo(tmp_path), use_cache=False)
    assert not any("node_modules" in f or "/ui_web/web/" in f for f in graph)


def test_cached_graph_after_an_edit_matches_a_fresh_build(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    first = build_graph(repo)
    assert (repo / ".pytest_cache" / "impacted_graph.json").is_file()
    assert build_graph(repo) == first
    edited = repo / "ducky_app/frontend/ui_web/terminal/manager.py"
    edited.write_text("from backend.tools import helpers\n", encoding="utf-8")
    st = edited.stat()
    os.utime(edited, ns=(st.st_atime_ns, st.st_mtime_ns + 5_000_000_000))
    _write(repo, {"ducky_app/backend/tools/new_mod.py": "", "release/publish.py": "import new_mod\n"})
    cached = build_graph(repo)
    assert cached == build_graph(repo, use_cache=False)
    assert "ducky_app/backend/tools/helpers.py" in cached["ducky_app/frontend/ui_web/terminal/manager.py"]
    assert cached["release/publish.py"] == set()  # release/new_mod.py does not exist


def test_a_broken_cache_is_ignored(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    (repo / ".pytest_cache").mkdir()
    (repo / ".pytest_cache" / "impacted_graph.json").write_text("{not json", encoding="utf-8")
    assert build_graph(repo) == build_graph(repo, use_cache=False)


def _select_repo(tmp_path: Path) -> Path:
    _write(tmp_path, {
        "pytest.ini": "[pytest]\n",
        "conftest.py": "",
        "ducky_app/test_file_sizes.py": "",
        "ducky_app/test_import_boundaries.py": "",
        "ducky_app/frontend/__init__.py": "",
        "ducky_app/frontend/test_settings_schema.py": "",
        "ducky_app/frontend/ui_web/__init__.py": "",
        "ducky_app/frontend/ui_web/terminal/__init__.py": "",
        "ducky_app/frontend/ui_web/terminal/shells.py": "",
        "ducky_app/frontend/ui_web/terminal/manager.py": "from . import shells\n",
        "ducky_app/frontend/ui_web/terminal/test_shells.py": "from frontend.ui_web.terminal import shells\n",
        "ducky_app/frontend/ui_web/terminal/test_unrelated.py": "",
        "ducky_app/backend/__init__.py": "",
        "ducky_app/backend/tools/__init__.py": "",
        "ducky_app/backend/tools/conftest.py": "",
        "ducky_app/backend/tools/test_plain.py": "",
        "ducky_app/backend/tools/panel/__init__.py": "",
        "ducky_app/backend/tools/panel/test_fwd.py": "def test_x():\n    from frontend.ui_web.terminal import manager\n",
        "ducky_app/backend/cyc_a.py": "from backend import cyc_b\n",
        "ducky_app/backend/cyc_b.py": "from backend import cyc_a\n",
        "ducky_app/backend/test_cyc.py": "import backend.cyc_a\n",
        "ducky_app/backend/old_mod.py": "",
        "ducky_app/backend/test_old.py": "from backend import old_mod\n",
    })
    return tmp_path


ALWAYS = {"ducky_app/test_file_sizes.py", "ducky_app/test_import_boundaries.py", "ducky_app/frontend/test_settings_schema.py"}


def test_an_indirect_importer_in_another_folder_is_selected(tmp_path: Path) -> None:
    repo = _select_repo(tmp_path)
    sel = impacted(repo, ["frontend/ui_web/terminal/shells.py"])
    assert not sel.run_all
    fwd = "ducky_app/backend/tools/panel/test_fwd.py"
    assert fwd in sel.tests and "ducky_app/frontend/ui_web/terminal/test_shells.py" in sel.tests
    assert "ducky_app/frontend/ui_web/terminal/test_unrelated.py" not in sel.tests
    folder_rule = {p.relative_to(repo).as_posix() for p in (repo / "ducky_app/frontend/ui_web/terminal").glob("test_*.py")}
    assert fwd not in folder_rule  # what the old rule missed
    assert set(sel.tests) - folder_rule == {fwd} | ALWAYS


def test_an_import_cycle_ends_and_selects_its_tests(tmp_path: Path) -> None:
    sel = impacted(_select_repo(tmp_path), ["ducky_app/backend/cyc_b.py"])
    assert set(sel.tests) == {"ducky_app/backend/test_cyc.py"} | ALWAYS


def test_a_changed_test_and_a_folder_conftest(tmp_path: Path) -> None:
    repo = _select_repo(tmp_path)
    assert "ducky_app/backend/tools/test_plain.py" in impacted(repo, [str(repo / "ducky_app/backend/tools/test_plain.py")]).tests
    sel = impacted(repo, ["ducky_app/backend/tools/conftest.py"])
    assert not sel.run_all
    assert set(sel.tests) == {"ducky_app/backend/tools/test_plain.py", "ducky_app/backend/tools/panel/test_fwd.py"} | ALWAYS


@pytest.mark.parametrize("changed, extra", [
    ("pytest.ini", {}),
    ("requirements-dev.txt", {"requirements-dev.txt": "pytest\n"}),
    ("conftest.py", {}),
    ("ducky_app/backend/loader.py", {"ducky_app/backend/loader.py": "import importlib.util as u\nu.spec_from_file_location('x', 'x.py')\n"}),
    ("ducky_app/backend/runner.py", {"ducky_app/backend/runner.py": "import runpy\n"}),
    ("ducky_app/backend/plugins.py", {"ducky_app/backend/plugins.py": "import importlib\ndef f(n):\n    importlib.import_module(n)\n"}),
])
def test_each_run_all_trigger(tmp_path: Path, changed: str, extra: dict[str, str]) -> None:
    repo = _select_repo(tmp_path)
    _write(repo, extra)
    sel = impacted(repo, [changed])
    assert sel.run_all and changed in sel.reason
    assert "ducky_app/backend/tools/test_plain.py" in sel.tests


def test_an_unrelated_change_still_runs_the_always_run_set(tmp_path: Path) -> None:
    sel = impacted(_select_repo(tmp_path), ["ducky_app/frontend/ui_web/terminal/test_unrelated.py"])
    assert set(sel.tests) == {"ducky_app/frontend/ui_web/terminal/test_unrelated.py"} | ALWAYS


def test_a_deleted_module_selects_its_old_importers(tmp_path: Path) -> None:
    repo = _select_repo(tmp_path)
    impacted(repo, [])  # an earlier run leaves the cached graph
    (repo / "ducky_app/backend/old_mod.py").unlink()
    sel = impacted(repo, ["ducky_app/backend/old_mod.py"])
    assert not sel.run_all and "ducky_app/backend/test_old.py" in sel.tests


def test_a_deleted_module_with_no_cached_graph_runs_everything(tmp_path: Path) -> None:
    repo = _select_repo(tmp_path)
    (repo / "ducky_app/backend/old_mod.py").unlink()
    assert impacted(repo, ["ducky_app/backend/old_mod.py"]).run_all


def test_the_real_repo_terminal_manager_reaches_the_panel_forwarding_test() -> None:
    sel = impacted(Path(__file__).resolve().parent.parent, ["ducky_app/frontend/ui_web/terminal/manager.py"])
    assert not sel.run_all, sel.reason
    assert "ducky_app/backend/tools/panel/test_terminal_forwarding.py" in sel.tests
