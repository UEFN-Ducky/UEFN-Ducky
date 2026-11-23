"""A module written across several files is compiled as the one module it is, and a build that
does not load is never written."""

from __future__ import annotations

import importlib.util
import io
import shutil
import sys
import zipfile
from pathlib import Path

import pytest

from backend.uefn_plugins import compile as build_engine
from backend.uefn_plugins import one_module
from backend.uefn_plugins.plugin_lint import check_backend

HOST = '''"""A toy."""
from __future__ import annotations

import os

BEFORE = 1
_PARTS = (
    "_a.py",
    "_b.py",
)  # ducky-build: one module
for _part in _PARTS:
    _path = os.path.join(os.path.dirname(__file__), _part)
    with open(_path, encoding="utf-8") as _fh:
        exec(compile(_fh.read(), _path, "exec", dont_inherit=True), globals())
AFTER = A + B
'''
PART_A = '''"""Part a says what it is."""
from __future__ import annotations

A = BEFORE + 1


def total(x: NotAName = 0) -> int:
    return A + B + later() + x
'''
PART_B = '''from __future__ import annotations

B = A * 10


def later() -> int:
    return 100
'''


def _toy(root: Path, host: str = HOST, a: str = PART_A, b: str = PART_B) -> Path:
    pkg = root / "pkg"
    (pkg / "sub").mkdir(parents=True)
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "sub" / "__init__.py").write_text(host, encoding="utf-8")
    (pkg / "sub" / "_a.py").write_text(a, encoding="utf-8")
    (pkg / "sub" / "_b.py").write_text(b, encoding="utf-8")
    return pkg


def _run(folder: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, folder / "__init__.py", submodule_search_locations=[str(folder)])
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
    finally:
        sys.modules.pop(name, None)
    return module


def _what(module) -> dict:
    return {"A": module.A, "B": module.B, "AFTER": module.AFTER, "total": module.total(3), "doc": module.__doc__}


def test_a_module_written_across_files_becomes_the_one_module_it_is(tmp_path: Path) -> None:
    source = _toy(tmp_path / "source")
    built = _toy(tmp_path / "built")
    assert one_module.join(built) == ["sub/_a.py", "sub/_b.py"]
    # The parts are in the module now, not files beside it: nothing is left to read at run time.
    assert sorted(p.name for p in (built / "sub").iterdir()) == ["__init__.py"]
    from_source, from_build = _run(source / "sub", "toy_from_source"), _run(built / "sub", "toy_from_build")
    assert _what(from_build) == _what(from_source) == {
        "A": 2, "B": 20, "AFTER": 22, "total": 125, "doc": "Part a says what it is."}
    assert from_source._PARTS == ("_a.py", "_b.py") and from_build._PARTS == ()
    # A module with no mark is left exactly as it was.
    assert (built / "__init__.py").read_text(encoding="utf-8") == ""


@pytest.mark.parametrize("change, says", [
    (lambda sub: (sub / "_b.py").unlink(), "sub/__init__.py: _b.py is not beside __init__.py."),
    (lambda sub: (sub / "_b.py").write_text("B = A * 10\n", encoding="utf-8"),
     "sub/__init__.py: _b.py and __init__.py must start with the same `from __future__` lines"),
    (lambda sub: (sub / "_b.py").write_text(PART_B + '_MORE = ("_c.py",)  # ducky-build: one module\n', encoding="utf-8"),
     "sub/__init__.py: _b.py has parts of its own"),
    (lambda sub: (sub / "__init__.py").write_text(HOST.replace('"_b.py",', '"_a.py",'), encoding="utf-8"),
     "sub/__init__.py: _a.py is listed twice."),
    (lambda sub: (sub / "__init__.py").write_text(HOST.replace('"_b.py",', '"more/_b.py",'), encoding="utf-8"),
     "must be the name of a .py file beside __init__.py"),
    (lambda sub: (sub / "__init__.py").write_text("import os  # ducky-build: one module\n", encoding="utf-8"),
     'goes on one top-level line of the form NAME = ("a.py", "b.py")'),
])
def test_a_marked_module_that_cannot_be_built_says_what_to_change(tmp_path: Path, change, says: str) -> None:
    pkg = _toy(tmp_path)
    change(pkg / "sub")
    before = {p.relative_to(pkg).as_posix(): p.read_bytes() for p in pkg.rglob("*.py")}
    assert any(says in line for line in one_module.problems(pkg)), one_module.problems(pkg)
    with pytest.raises(one_module.OneModuleError, match="sub/__init__.py"):
        one_module.join(pkg)
    assert {p.relative_to(pkg).as_posix(): p.read_bytes() for p in pkg.rglob("*.py")} == before  # nothing half done


def test_the_words_of_the_mark_inside_a_string_are_not_a_mark() -> None:
    assert one_module.declared('HELP = "write # ducky-build: one module after the list"\n') is None
    assert one_module.declared("X = 1\n") is None


def test_the_plugin_check_takes_the_marked_list_and_refuses_the_same_code_without_it(tmp_path: Path) -> None:
    backend = _toy(tmp_path)
    backend.rename(tmp_path / "backend")
    sub = tmp_path / "backend" / "sub"
    reads = lambda: [line for line in check_backend(tmp_path, {}, "demo") if "backend/sub/" in line]  # noqa: E731
    assert reads() == []
    # A problem with the list is said by the check, before any build.
    (sub / "_b.py").unlink()
    assert reads() == ["backend/sub/__init__.py: _b.py is not beside __init__.py."]
    # Without the mark the build would leave the plugin reading files that are gone.
    (sub / "__init__.py").write_text(HOST.replace("  # ducky-build: one module", ""), encoding="utf-8")
    assert len(reads()) == 1 and "backend/sub/__init__.py:14: runs Python it reads from a file beside the module" in reads()[0]
    # ... and so would a path to a .py file kept in a name first.
    (sub / "__init__.py").write_text(
        "import os\n_path = os.path.join(os.path.dirname(__file__), '_a.py')\nTEXT = open(_path).read()\n", encoding="utf-8")
    assert len(reads()) == 1 and "backend/sub/__init__.py:3: reads a .py file." in reads()[0]


def test_the_build_engine_joins_the_staged_package_and_says_which_files(tmp_path: Path) -> None:
    source = _toy(tmp_path / "source")
    staged = tmp_path / "staged"
    build_engine._stage_backend_package(source, staged)
    assert one_module.join(staged) == ["sub/_a.py", "sub/_b.py"]
    assert (source / "sub" / "_a.py").is_file()  # the plugin's own files are never touched
    assert "A = BEFORE + 1" in (staged / "sub" / "__init__.py").read_text(encoding="utf-8")


def _zip_of(files: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, data in files.items():
            zf.writestr(name, data)
    return buf.getvalue()


def _real_extension() -> tuple[str, str, Path] | None:
    """(name, a function it has, its file): an extension module of this Python, in this OS's form."""
    ext = ".pyd" if sys.platform == "win32" else ".so"
    for name, func in (("winsound", "Beep"), ("_csv", "reader"), ("select", "select"), ("array", "array"),
                       ("_json", "scanstring"), ("math", "sqrt")):
        spec = importlib.util.find_spec(name)
        origin = Path(spec.origin or "") if spec else Path()
        if origin.suffix.lower() == ext:
            return name, func, origin
    return None


def test_a_build_is_loaded_before_it_is_written(tmp_path: Path) -> None:
    # A file that is no module at all: the load-check process starts on every OS, the load
    # fails, and the check says what Python said.
    ext = ".pyd" if sys.platform == "win32" else ".so"
    (tmp_path / "bad").mkdir()
    broken, unchecked = build_engine._check_build_loads(
        _zip_of({f"demo{ext}": b"not a module"}), tmp_path / "bad", "demo", f"demo{ext}", "register")
    assert broken.startswith("ImportError") and unchecked == ""
    # A real extension module of this Python stands in for a plugin's compiled backend.
    found = _real_extension()
    if found is None:
        pytest.skip("no extension module to load on this platform")
    name, func, origin = found
    good = _zip_of({f"{name}{ext}": origin.read_bytes(), "plugin.json": b"{}"})
    (tmp_path / "good").mkdir()
    assert build_engine._check_build_loads(good, tmp_path / "good", name, f"{name}{ext}", func) == ("", "")
    (tmp_path / "noreg").mkdir()
    broken, unchecked = build_engine._check_build_loads(good, tmp_path / "noreg", name, f"{name}{ext}", "register")
    assert (broken, unchecked) == ("the backend has no register()", "")
    shutil.rmtree(tmp_path, ignore_errors=True)
