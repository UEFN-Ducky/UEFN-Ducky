"""The panel's main thread does as little as it can before the window opens."""
import ast
import inspect
import sys
import textwrap

from frontend.ui_web import webview_app


def _run_panel_tree() -> ast.FunctionDef:
    source = textwrap.dedent(inspect.getsource(webview_app._run_panel))
    return ast.parse(source).body[0]


def _imports_outside_nested_functions(fn: ast.FunctionDef) -> set[str]:
    found: set[str] = set()

    def walk(node: ast.AST) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                continue  # runs later, on whatever thread calls it
            if isinstance(child, ast.ImportFrom) and child.module:
                found.add(child.module)
            elif isinstance(child, ast.Import):
                found.update(alias.name for alias in child.names)
            walk(child)

    walk(fn)
    return found


def test_main_thread_never_imports_the_mcp_tool_package():
    # backend.tools.core.* runs backend/tools/__init__.py, which registers every MCP tool
    # (~0.6 s before the window). The popup guard starts from the panel-startup-bg thread.
    imports = _imports_outside_nested_functions(_run_panel_tree())
    assert not {m for m in imports if m == "backend.tools" or m.startswith("backend.tools.")}
    assert "backend.server" not in imports


def test_popup_guard_still_starts_from_the_startup_thread():
    tree = _run_panel_tree()
    bg = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "_startup_bg")
    calls = {n.func.id for n in ast.walk(bg) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
    assert "start_popup_guard" in calls


def test_warmup_imports_on_a_worker_thread():
    sys.modules.pop("colorsys", None)
    thread = webview_app._start_import_warmup(("colorsys",))
    thread.join(timeout=10)
    assert not thread.is_alive()
    assert thread.daemon
    assert "colorsys" in sys.modules


def test_failed_warmup_import_never_raises():
    thread = webview_app._start_import_warmup(("no_such_module_for_ducky_warmup", "colorsys"))
    thread.join(timeout=10)
    assert not thread.is_alive()
    assert "no_such_module_for_ducky_warmup" not in sys.modules


def test_warmup_is_joined_before_webview_is_imported():
    source = inspect.getsource(webview_app._run_panel)
    join_at = source.index("webview_warmup.join(")
    assert join_at < source.index("import webview\n")
    assert join_at < source.index("install_pywebview_chrome_patches()")
    assert source.index("_start_import_warmup()") < source.index("from backend.workspace.diff_workers")
