"""Installed releases recover missing entrypoints and lazy assets safely."""
import json
import shutil
import zipfile

import pytest

from frontend.ui_web import panel_assets as pa


@pytest.fixture
def panel(tmp_path):
    root = tmp_path / "ui_web" / "web" / "dist"
    (root / "assets").mkdir(parents=True)
    (root / "index.html").write_text(
        '<script src="./assets/main.js"></script><link href="./assets/main.css">',
        encoding="utf-8",
    )
    (root / "assets" / "main.js").write_text("import('./lazy.js')", encoding="utf-8")
    (root / "assets" / "main.css").write_text("body{}", encoding="utf-8")
    (root / "assets" / "lazy.js").write_text("export default 42", encoding="utf-8")
    archive = root.parent.parent / "panel-dist.zip"
    pa.create_panel_archive(root, archive)
    yield root, archive
    recovery = pa._recovered.pop(root.resolve(), None)
    if recovery:
        recovery.cleanup()


def test_complete_install_uses_original_files(panel):
    root, archive = panel
    pa.verify_bundled_panel(root, archive)
    assert pa.ensure_panel_dist(root) == root.resolve()


@pytest.mark.parametrize("missing", ["index.html", "assets/main.js", "assets/lazy.js"])
def test_missing_installed_file_recovers_whole_matching_release(panel, missing):
    root, archive = panel
    expected = (root / missing).read_bytes()
    (root / missing).unlink()
    with pytest.raises((OSError, ValueError)):
        pa.verify_bundled_panel(root, archive)
    recovered = pa.ensure_panel_dist(root)
    assert recovered != root.resolve()
    assert (recovered / missing).read_bytes() == expected
    pa.verify_bundled_panel(recovered, archive)
    assert pa.ensure_panel_dist(root) == recovered
    assert not (root / missing).exists()  # no write permission needed in Program Files


def test_equal_size_corrupted_js_is_recovered(panel):
    root, archive = panel
    path = root / "assets" / "main.js"
    expected = path.read_bytes()
    path.write_bytes(b"x" * len(expected))
    recovered = pa.ensure_panel_dist(root)
    assert (recovered / "assets" / "main.js").read_bytes() == expected


def test_no_archive_keeps_source_validation(panel):
    root, archive = panel
    archive.unlink()
    assert pa.ensure_panel_dist(root) == root.resolve()
    (root / "assets" / "main.js").unlink()
    with pytest.raises(FileNotFoundError, match="Panel build incomplete"):
        pa.ensure_panel_dist(root)


@pytest.mark.parametrize("name", ["../escape.js", "/absolute.js", "C:/escape.js", "..\\\\escape.js"])
def test_archive_cannot_escape_recovery_directory(panel, name):
    root, archive = panel
    with zipfile.ZipFile(archive, "a") as bundle:
        entry = zipfile.ZipInfo("placeholder.js")
        entry.filename = name
        bundle.writestr(entry, "untrusted")
    with pytest.raises(ValueError, match="Invalid panel archive"):
        pa.ensure_panel_dist(root)
    assert root.resolve() not in pa._recovered


def test_installer_check_never_accepts_missing_asset(panel, tmp_path, monkeypatch):
    root, archive = panel
    # Put the fixture at the packaged layout expected by the installer check.
    data_root = tmp_path / "package"
    destination = data_root / "frontend" / "ui_web"
    shutil.copytree(root.parent.parent, destination)
    monkeypatch.setattr("frontend.bundle_root.packaged_data_root", lambda: data_root)
    output = tmp_path / "result.json"
    assert pa.check_installed_panel(str(output)) == 0
    assert json.loads(output.read_text())["ok"]
    (destination / "web" / "dist" / "assets" / "lazy.js").unlink()
    assert pa.check_installed_panel(str(output)) == 1
    assert not json.loads(output.read_text())["ok"]


def test_corrupt_archive_is_not_used_for_recovery(panel):
    root, archive = panel
    (root / "assets" / "main.js").unlink()
    archive.write_bytes(b"broken")
    with pytest.raises(zipfile.BadZipFile):
        pa.ensure_panel_dist(root)
