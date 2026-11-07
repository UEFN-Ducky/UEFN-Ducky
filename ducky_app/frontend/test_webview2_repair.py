"""fbx4k9m2w7q3d: an interrupted Setup left _internal/webview/lib without Microsoft.Web.WebView2.Core.dll."""

from pathlib import Path

from frontend import webview2_repair as repair


def _app(root: Path, names=repair.DLLS) -> Path:
    for name in names:
        dll = root / repair.LIB / name
        dll.parent.mkdir(parents=True, exist_ok=True)
        dll.write_bytes(b"dll " + name.encode())
    return root


def test_a_whole_install_needs_nothing(tmp_path: Path) -> None:
    app = _app(tmp_path / "app")
    assert repair.ensure(app, sources=[]) == []


def test_a_missing_dll_is_copied_from_another_version(tmp_path: Path) -> None:
    app = _app(tmp_path / "app", repair.DLLS[1:])
    other = _app(tmp_path / "versions" / "app-1.2.470")
    assert repair.missing(app) == ["Microsoft.Web.WebView2.Core.dll"]
    assert repair.ensure(app, sources=[app, other]) == []
    assert (app / repair.LIB / repair.DLLS[0]).read_bytes() == b"dll Microsoft.Web.WebView2.Core.dll"


def test_no_copy_anywhere_leaves_it_to_the_repair_message(tmp_path: Path) -> None:
    from frontend import bundle_root
    from frontend.ui_web import shutdown

    app = _app(tmp_path / "app", repair.DLLS[1:])
    assert repair.ensure(app, sources=[app, tmp_path / "nothing"]) == ["Microsoft.Web.WebView2.Core.dll"]
    original = bundle_root.is_packaged_runtime
    bundle_root.is_packaged_runtime = lambda: True
    try:
        text = shutdown._format_fatal_message(FileNotFoundError("Cannot find Microsoft.Web.WebView2.Core.dll"))
    finally:
        bundle_root.is_packaged_runtime = original
    assert "Run the latest UEFN Ducky Setup" in text


def test_a_source_run_is_left_alone() -> None:
    assert repair.ensure() == []
