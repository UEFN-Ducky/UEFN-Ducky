"""The installer ships, signs and closes the optional native launcher (Ducky-Launcher.exe)
without touching shortcuts, AppId or uninstall."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

RELEASE = Path(__file__).resolve().parent
ISS = (RELEASE / "installer" / "UEFN-Ducky.iss").read_text(encoding="utf-8")

# Pinned lines: an update must keep the same AppId, payload entry, shortcuts and uninstall
# key, or Windows sees a second app (or loses the launcher beside UEFN-Ducky.exe).
PINNED = [
    '#define MyAppId "EAD694ED-E221-40B0-909B-AFFD7F683C9E"',
    "AppId={{{#MyAppId}}",
    '#define MyAppExeName "UEFN-Ducky.exe"',
    'Source: "{#MyAppDir}\\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs',
    'Name: "{autoprograms}\\{#MyAppName}"; Filename: "{app}\\{#MyAppExeName}"',
    'Name: "{autodesktop}\\{#MyAppName}"; Filename: "{app}\\{#MyAppExeName}"; Tasks: desktopicon',
    "  UninstallRegKey = 'Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\{{#MyAppId}}_is1';",
]


def test_pinned_lines_are_unchanged():
    lines = ISS.splitlines()
    for line in PINNED:
        assert line in lines, line


def test_launcher_is_optional_no_named_file_entry():
    # It rides the whole-folder entry: a build without it still packs (no missing-file error).
    assert "Ducky-Launcher.exe" not in "".join(l for l in ISS.splitlines() if l.startswith("Source:"))


def test_close_kills_the_launcher_with_the_app_and_bridge():
    kill = next(l for l in ISS.splitlines() if "taskkill.exe" in l)
    for name in ("UEFN-Ducky.exe", "UEFN-Ducky-Bridge.exe", "Ducky-Launcher.exe"):
        assert f"/IM {name}" in kill
    assert "/T" not in kill.split("'")[3]  # never the process tree: Setup is Ducky's child


def _publish_app():
    sys.path.insert(0, str(RELEASE))
    spec = importlib.util.spec_from_file_location("publish_app_installer_test", RELEASE / "publish_app.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _touch(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"MZ")


def test_signer_gets_every_payload_exe_including_the_launcher(tmp_path, monkeypatch):
    pub = _publish_app()
    monkeypatch.setattr(pub, "ROOT", tmp_path)
    app = tmp_path / "dist" / "UEFN-Ducky-1.2.3"
    for name in ("UEFN-Ducky.exe", "UEFN-Ducky-Bridge.exe", "Ducky-Launcher.exe"):
        _touch(app / name)
    assert [p.name for p in pub.payload_exes("1.2.3")] == [
        "Ducky-Launcher.exe", "UEFN-Ducky-Bridge.exe", "UEFN-Ducky.exe",
    ]


def test_signer_list_without_the_launcher_still_works(tmp_path, monkeypatch):
    pub = _publish_app()
    monkeypatch.setattr(pub, "ROOT", tmp_path)
    app = tmp_path / "dist" / "UEFN-Ducky-1.2.3"
    _touch(app / "UEFN-Ducky.exe")
    assert [p.name for p in pub.payload_exes("1.2.3")] == ["UEFN-Ducky.exe"]


# --- update pack ---------------------------------------------------------------


def _update_pack():
    spec = importlib.util.spec_from_file_location("update_pack_installer_test", RELEASE / "update_pack.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _pack(tmp_path: Path, *, launcher: bool) -> dict:
    app = tmp_path / "UEFN-Ducky-1.2.3"
    _touch(app / "UEFN-Ducky.exe")
    _touch(app / "_internal" / "base.dll")
    if launcher:
        _touch(app / "Ducky-Launcher.exe")
    out = tmp_path / "dist"
    out.mkdir()
    return _update_pack().build_update_pack(app, "1.2.3", out)[2]


def test_pack_carries_the_launcher_marked_optional(tmp_path):
    files = {e["path"]: e for e in _pack(tmp_path, launcher=True)["files"]}
    assert files["Ducky-Launcher.exe"]["optional"] is True
    assert "optional" not in files["UEFN-Ducky.exe"] and "optional" not in files["_internal/base.dll"]


def test_pack_without_the_launcher_has_no_entry(tmp_path):
    files = _pack(tmp_path, launcher=False)["files"]
    assert [e["path"] for e in files] == ["UEFN-Ducky.exe", "_internal/base.dll"]


def test_the_shipped_reader_accepts_a_manifest_with_the_optional_key(tmp_path):
    # Old-reader fixture: app_stage.read_manifest as installed Duckies run it today.
    import io
    import json
    import zipfile

    sys.path.insert(0, str(RELEASE.parent / "ducky_app"))
    from frontend import app_stage

    manifest = _pack(tmp_path, launcher=True)
    raw = io.BytesIO()
    with zipfile.ZipFile(raw, "w") as bundle:
        bundle.writestr("manifest.json", json.dumps(manifest))
    assert app_stage.read_manifest(raw.getvalue(), version="1.2.3") == manifest
