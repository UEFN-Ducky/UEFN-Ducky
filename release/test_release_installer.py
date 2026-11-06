"""The installer ships, signs and closes the native launcher (Ducky-Launcher.exe), and its
shortcuts and Open with commands start it; AppId, icons and uninstall are unchanged."""

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
    'UninstallDisplayIcon={app}\\{#MyAppExeName}',
    "  UninstallRegKey = 'Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\{{#MyAppId}}_is1';",
]


def test_pinned_lines_are_unchanged():
    lines = ISS.splitlines()
    for line in PINNED:
        assert line in lines, line


# The launch lines before the Repoint, exactly as 1.2.47x shipped them.
PINNED_TARGETS = [
    'Name: "{autoprograms}\\{#MyAppName}"; Filename: "{app}\\{#MyAppExeName}"',
    'Name: "{autodesktop}\\{#MyAppName}"; Filename: "{app}\\{#MyAppExeName}"; Tasks: desktopicon',
    'Root: HKA; Subkey: "Software\\Classes\\Applications\\{#MyAppExeName}\\shell\\open\\command"; ValueType: string; ValueName: ""; ValueData: """{app}\\{#MyAppExeName}"" ""%1"""',
    'Root: HKA; Subkey: "Software\\Classes\\*\\shell\\UEFNDucky\\command"; ValueType: string; ValueName: ""; ValueData: """{app}\\{#MyAppExeName}"" ""%1"""',
]


def _repointed(line: str) -> str:
    """The pinned line with only its target swapped (shortcuts keep the app's icon)."""
    if line.startswith("Name:"):
        return line.replace(
            'Filename: "{app}\\{#MyAppExeName}"',
            'Filename: "{app}\\{#MyLauncherExeName}"; IconFilename: "{app}\\{#MyAppExeName}"',
        )
    return line.replace('"""{app}\\{#MyAppExeName}""', '"""{app}\\{#MyLauncherExeName}""')


def test_launch_lines_differ_from_the_pinned_copy_only_in_their_target():
    lines = ISS.splitlines()
    assert '#define MyLauncherExeName "Ducky-Launcher.exe"' in lines
    for old in PINNED_TARGETS:
        assert old not in lines, old
        assert _repointed(old) in lines, _repointed(old)
    # Key names, icons and the friendly name stay on UEFN-Ducky.exe.
    for keep in ("Applications\\{#MyAppExeName}\\DefaultIcon", "*\\OpenWithList\\{#MyAppExeName}",
                 'ValueName: "Icon"; ValueData: "{app}\\{#MyAppExeName}"'):
        assert keep in ISS, keep
    # The finish-page launch starts the launcher too.
    assert "ExpandConstant('{app}\\{#MyLauncherExeName}')" in ISS


def test_verify_accepts_this_script_and_rejects_the_old_targets():
    vip = _vip()
    vip.check_launch_targets(ISS)
    old = ISS
    for line in PINNED_TARGETS:
        old = old.replace(_repointed(line), line)
    with pytest.raises(RuntimeError, match="does not start"):
        vip.check_launch_targets(old)
    with pytest.raises(RuntimeError, match="MyLauncherExeName"):
        vip.check_launch_targets(ISS.replace('"Ducky-Launcher.exe"', '"UEFN-Ducky.exe"'))


@pytest.mark.parametrize("gone", ["Ducky-Launcher.exe", "WebView2Loader.dll"])
def test_verify_fails_without_either_root_file(tmp_path, gone):
    vip, app = _vip(), _extracted(tmp_path, True)  # app/ is the install root
    (app / gone).unlink()
    with pytest.raises(RuntimeError, match="missing|without"):
        vip.check_launcher(app, "1.2.3", signed=False, run=_Ran("1.2.3"))


def test_launcher_is_optional_no_named_file_entry():
    # It rides the whole-folder entry: a build without it still packs (no missing-file error).
    sources = "".join(l for l in ISS.splitlines() if l.startswith("Source:"))
    assert "Ducky-Launcher.exe" not in sources and "WebView2Loader.dll" not in sources


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
    for name in ("UEFN-Ducky.exe", "UEFN-Ducky-Bridge.exe", "Ducky-Launcher.exe", "WebView2Loader.dll"):
        _touch(app / name)
    # The Microsoft-signed WebView2Loader.dll is never re-signed: only EXEs go to the signer.
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
        _touch(app / "WebView2Loader.dll")
    out = tmp_path / "dist"
    out.mkdir()
    return _update_pack().build_update_pack(app, "1.2.3", out)[2]


def test_pack_carries_the_launcher_marked_optional(tmp_path):
    files = {e["path"]: e for e in _pack(tmp_path, launcher=True)["files"]}
    assert files["Ducky-Launcher.exe"]["optional"] is True
    assert files["WebView2Loader.dll"]["optional"] is True
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
        _touch(app / "WebView2Loader.dll")
    return app


def test_launcher_without_its_dll_fails(tmp_path):
    vip, app, run = _vip(), _extracted(tmp_path, True), _Ran("1.2.3")
    (app / "WebView2Loader.dll").unlink()
    with pytest.raises(RuntimeError, match="WebView2Loader.dll"):
        vip.check_launcher(app, "1.2.3", signed=False, run=run)
    assert run.calls == []


def test_launcher_present_signed_and_version_matches(tmp_path):
    vip, app, run = _vip(), _extracted(tmp_path, True), _Ran("1.2.3\r\n")
    vip.check_launcher(app, "1.2.3", signed=True, run=run, signature=lambda p: "Valid")
    assert run.calls == [[str(app / "Ducky-Launcher.exe"), "--version"]]


def test_launcher_unsigned_fails_when_signing_is_expected(tmp_path):
    vip, app = _vip(), _extracted(tmp_path, True)
    with pytest.raises(RuntimeError, match="not signed"):
        vip.check_launcher(app, "1.2.3", signed=True, run=_Ran("1.2.3"), signature=lambda p: "NotSigned")
    # An unsigned release never asks for the signature.
    vip.check_launcher(app, "1.2.3", signed=False, run=_Ran("1.2.3"), signature=None)


@pytest.mark.parametrize("stdout,code", [("1.2.2", 0), ("1.2.3", 1), ("", 0)])
def test_launcher_wrong_version_or_exit_fails(tmp_path, stdout, code):
    vip, app = _vip(), _extracted(tmp_path, True)
    with pytest.raises(RuntimeError, match="--version"):
        vip.check_launcher(app, "1.2.3", signed=False, run=_Ran(stdout, code))


def test_launcher_absent_always_fails(tmp_path):
    vip, app, run = _vip(), _extracted(tmp_path, False), _Ran("1.2.3")
    with pytest.raises(RuntimeError, match="missing"):
        vip.check_launcher(app, "1.2.3", signed=False, run=run)
    assert run.calls == []


def test_fresh_settings_default_off():
    vip = _vip()
    vip.check_default_off({"ok": True, "native_launcher": "off"})
    for report in ({"ok": True}, {"ok": True, "native_launcher": "auto"}):
        with pytest.raises(RuntimeError, match="native_launcher"):
            vip.check_default_off(report)


@pytest.mark.parametrize("status,want", [("Valid", True), ("NotSigned", False), ("Unknown", False)])
def test_signature_expected_exactly_when_the_app_exe_is_signed(tmp_path, status, want):
    vip, app = _vip(), _extracted(tmp_path, True)
    seen = []
    assert vip.expect_signed(app, None, signature=lambda p: seen.append(p.name) or status) is want
    assert seen == ["UEFN-Ducky.exe"]


def test_signed_flag_overrides_the_auto_decision(tmp_path):
    vip, app = _vip(), _extracted(tmp_path, True)
    never = lambda p: pytest.fail("signature probed despite the flag")  # noqa: E731
    assert vip.expect_signed(app, False, signature=never) is False
    assert vip.expect_signed(app, True, signature=never) is True


def test_cli_passes_only_the_flags_given(tmp_path, monkeypatch):
    vip, got = _vip(), []
    monkeypatch.setattr(vip, "verify", lambda extractor, engine, **kw: got.append(kw))
    tool = str(tmp_path / "innoextract.exe")
    for argv in ([], ["--no-expect-signed"]):
        monkeypatch.setattr(sys, "argv", ["verify_installer_panel.py", "--extractor", tool, *argv])
        vip.main()
    assert got == [{}, {"signed": False}]


@pytest.mark.parametrize("flag", ["--no-require-launcher", "--require-launcher", "--allow-no-launcher"])
def test_there_is_no_launcher_opt_out_flag(tmp_path, monkeypatch, flag):
    vip = _vip()
    monkeypatch.setattr(vip, "verify", lambda *a, **k: pytest.fail("ran despite an unknown flag"))
    monkeypatch.setattr(sys, "argv", ["verify_installer_panel.py", "--extractor", str(tmp_path / "x.exe"), flag])
    with pytest.raises(SystemExit) as stop:
        vip.main()
    assert stop.value.code == 2


def test_app_version_reads_the_repo_init(tmp_path):
    init = tmp_path / "ducky_app" / "frontend" / "__init__.py"
    init.parent.mkdir(parents=True)
    init.write_text('"""x"""\n__version__ = "1.2.471"\n', encoding="utf-8")
    assert _vip().app_version(tmp_path) == "1.2.471"
