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


def test_panel_api_starts_while_webview_still_warms():
    # The panel API (~0.2-0.35 s) needs no webview: it overlaps the .NET load instead of
    # waiting behind the join.
    source = inspect.getsource(webview_app._run_panel)
    join_at = source.index("webview_warmup.join(")
    assert source.index("from frontend.ui_web.panel_api import shared_panel_api") < join_at
    assert source.index("api = shared_panel_api()") < join_at


def _calls_outside_nested_functions(fn: ast.FunctionDef) -> set[str]:
    found: set[str] = set()

    def walk(node: ast.AST) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                continue
            if isinstance(child, ast.Call):
                f = child.func
                found.add(f.id if isinstance(f, ast.Name) else getattr(f, "attr", ""))
            walk(child)

    walk(fn)
    return found


def test_background_startup_work_waits_for_the_window():
    # Popup guard (MCP tool imports), IDE/skill shipping, Store updates, scheduler, sync and
    # presence shared the GIL with the boot imports and the page's first calls.
    calls = _calls_outside_nested_functions(_run_panel_tree())
    for deferred in ("ship_newest_everywhere_async", "start_appdata_maintenance_async", "start_popup_guard"):
        assert deferred not in calls
    source = inspect.getsource(webview_app._run_panel)
    shown = source[source.index("def _on_shown"):]
    assert '_start_later(background_work["start"], _BACKGROUND_AFTER_SHOWN_S)' in shown[: shown.index("\n    def ") if "\n    def " in shown else None]
    # A hidden start never fires "shown": the fallback still starts it before webview.start.
    fallback_at = source.index('_start_later(background_work["start"], _BACKGROUND_FALLBACK_S)')
    assert fallback_at < source.index("webview.start(")
    assert webview_app._BACKGROUND_AFTER_SHOWN_S <= 3 and webview_app._BACKGROUND_FALLBACK_S <= 10


def test_deferred_work_runs_once_whoever_calls_first():
    import threading

    ran: list[int] = []
    start = webview_app._once(lambda: ran.append(1))
    threads = [threading.Thread(target=start) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=5)
    start()
    assert ran == [1]


def test_deferred_work_failure_never_raises():
    def boom() -> None:
        raise RuntimeError("x")

    webview_app._once(boom)()


def test_start_later_fires_on_a_daemon_timer():
    import threading

    fired = threading.Event()
    webview_app._start_later(fired.set, 0.01)
    assert fired.wait(5)


def test_sign_in_start_opens_hidden_and_never_focuses_an_open_ducky():
    run_src = inspect.getsource(webview_app.run)
    quiet_exit = run_src.index("started_in_background() and living_pid is not None")
    assert quiet_exit < run_src.index("_hand_off_to_living_panel(")
    panel_src = inspect.getsource(webview_app._run_panel)
    assert "start_hidden = bool(handover.get(\"hidden\")) or started_in_background()" in panel_src
    assert "hidden=start_hidden" in panel_src


def test_a_test_copy_registers_nothing_in_hkcu():
    run_src = inspect.getsource(webview_app.run)
    reg = run_src[run_src.index("def _register_shell_integrations"):]
    guard = reg.index('if os.environ.get("DUCKY_SANDBOX")')
    assert guard < reg.index("register_windows_open_with()")
    assert guard < reg.index("apply_saved_setting()")


def test_panel_api_loads_no_webview():
    import subprocess

    code = (
        "import sys; import frontend.ui_web.panel_api; "
        "print(any(m in sys.modules for m in ('webview', 'clr', 'webview.platforms.winforms')))"
    )
    out = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        cwd=str(__import__("pathlib").Path(webview_app.__file__).resolve().parents[2]),
        timeout=120,
    )
    assert out.returncode == 0, out.stderr[-2000:]
    assert out.stdout.strip().splitlines()[-1] == "False"
