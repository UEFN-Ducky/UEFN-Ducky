"""The installer ships, signs and closes the optional native launcher (Ducky-Launcher.exe)
without touching shortcuts, AppId or uninstall."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

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


# --- verify_installer_panel: the extracted launcher ----------------------------------

def _vip():
    spec = importlib.util.spec_from_file_location("vip_installer_test", RELEASE / "verify_installer_panel.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class _Ran:
    def __init__(self, stdout: str, code: int = 0):
        self.stdout, self.returncode, self.calls = stdout, code, []

    def __call__(self, cmd, **kw):
        self.calls.append(cmd)
        return self


def _extracted(tmp_path: Path, launcher: bool) -> Path:
    app = tmp_path / "app"
    _touch(app / "UEFN-Ducky.exe")
    if launcher:
        _touch(app / "Ducky-Launcher.exe")
    return app


def test_launcher_present_signed_and_version_matches(tmp_path):
    vip, app, run = _vip(), _extracted(tmp_path, True), _Ran("1.2.3\r\n")
    assert vip.check_launcher(app, "1.2.3", signed=True, required=True, run=run, signature=lambda p: "Valid")
    assert run.calls == [[str(app / "Ducky-Launcher.exe"), "--version"]]


def test_launcher_unsigned_fails_when_signing_is_expected(tmp_path):
    vip, app = _vip(), _extracted(tmp_path, True)
    with pytest.raises(RuntimeError, match="not signed"):
        vip.check_launcher(app, "1.2.3", signed=True, required=True, run=_Ran("1.2.3"), signature=lambda p: "NotSigned")
    # An unsigned release never asks for the signature.
    assert vip.check_launcher(app, "1.2.3", signed=False, required=True, run=_Ran("1.2.3"), signature=None)


@pytest.mark.parametrize("stdout,code", [("1.2.2", 0), ("1.2.3", 1), ("", 0)])
def test_launcher_wrong_version_or_exit_fails(tmp_path, stdout, code):
    vip, app = _vip(), _extracted(tmp_path, True)
    with pytest.raises(RuntimeError, match="--version"):
        vip.check_launcher(app, "1.2.3", signed=False, required=True, run=_Ran(stdout, code))


def test_launcher_absent_is_skipped_unless_required(tmp_path):
    vip, app, run = _vip(), _extracted(tmp_path, False), _Ran("1.2.3")
    assert vip.check_launcher(app, "1.2.3", signed=True, required=False, run=run) is False
    assert run.calls == []
    with pytest.raises(RuntimeError, match="missing"):
        vip.check_launcher(app, "1.2.3", signed=True, required=True, run=run)


def test_fresh_settings_default_off():
    vip = _vip()
    vip.check_default_off({"ok": True, "native_launcher": "off"})
    for report in ({"ok": True}, {"ok": True, "native_launcher": "auto"}):
        with pytest.raises(RuntimeError, match="native_launcher"):
            vip.check_default_off(report)


def _dist(tmp_path: Path, launcher: bool) -> Path:
    dist_app = tmp_path / "dist" / "UEFN-Ducky-1.2.3"
    _touch(dist_app / "UEFN-Ducky.exe")
    if launcher:
        _touch(dist_app / "Ducky-Launcher.exe")
    return dist_app


@pytest.mark.parametrize("built", [True, False])
def test_launcher_required_exactly_when_the_build_made_one(tmp_path, built):
    vip, app = _vip(), _extracted(tmp_path, False)
    _, required = vip.expectations(app, _dist(tmp_path, built), signed=False, require_launcher=None)
    assert required is built
    if built:  # the payload lost it: fails
        with pytest.raises(RuntimeError, match="missing"):
            vip.check_launcher(app, "1.2.3", signed=False, required=required, run=_Ran("1.2.3"))


@pytest.mark.parametrize("status,want", [("Valid", True), ("NotSigned", False), ("Unknown", False)])
def test_signature_expected_exactly_when_the_app_exe_is_signed(tmp_path, status, want):
    vip, app = _vip(), _extracted(tmp_path, True)
    seen = []
    signed, _ = vip.expectations(app, _dist(tmp_path, True), signed=None, require_launcher=None,
                                 signature=lambda p: seen.append(p.name) or status)
    assert signed is want and seen == ["UEFN-Ducky.exe"]


def test_flags_override_the_auto_decisions(tmp_path):
    vip, app = _vip(), _extracted(tmp_path, False)
    never = lambda p: pytest.fail("signature probed despite the flag")  # noqa: E731
    assert vip.expectations(app, _dist(tmp_path, True), signed=False, require_launcher=False, signature=never) == (False, False)
    assert vip.expectations(app, _dist(tmp_path, False), signed=True, require_launcher=True, signature=never) == (True, True)


def test_cli_passes_only_the_flags_given(tmp_path, monkeypatch):
    vip, got = _vip(), []
    monkeypatch.setattr(vip, "verify", lambda extractor, engine, **kw: got.append(kw))
    tool = str(tmp_path / "innoextract.exe")
    for argv in ([], ["--no-expect-signed", "--require-launcher"]):
        monkeypatch.setattr(sys, "argv", ["verify_installer_panel.py", "--extractor", tool, *argv])
        vip.main()
    assert got == [{}, {"signed": False, "require_launcher": True}]


def test_app_version_reads_the_repo_init(tmp_path):
    init = tmp_path / "ducky_app" / "frontend" / "__init__.py"
    init.parent.mkdir(parents=True)
    init.write_text('"""x"""\n__version__ = "1.2.471"\n', encoding="utf-8")
    assert _vip().app_version(tmp_path) == "1.2.471"
