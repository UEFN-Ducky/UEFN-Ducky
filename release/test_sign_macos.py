import base64
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "release"))
import sign_macos as sm  # noqa: E402

MACHO = bytes.fromhex("cffaedfe") + b"\0" * 12

SIGNED_ENV = {
    "MACOS_CERT_P12_B64": base64.b64encode(b"p12").decode(),
    "MACOS_CERT_PASSWORD": "pw",
    "APPLE_TEAM_ID": "ABCDE12345",
}
NOTARY_ENV = {"ASC_KEY_ID": "KEY123", "ASC_ISSUER_ID": "issuer-uuid", "ASC_KEY_P8_B64": base64.b64encode(b"p8").decode()}


class FakeRun:
    """Records every argv; ``notary`` is what ``notarytool submit`` prints."""

    def __init__(self, notary: str = "Accepted") -> None:
        self.calls: list[list[str]] = []
        self.notary = notary

    def __call__(self, cmd, **kw):
        self.calls.append([str(c) for c in cmd])
        out = ""
        if cmd[:3] == ["xcrun", "notarytool", "submit"]:
            out = json.dumps({"id": "sub-1", "status": self.notary})
        return type("P", (), {"returncode": 0, "stdout": out, "stderr": ""})()

    def tools(self) -> list[str]:
        return [" ".join(c[:2]) for c in self.calls]


def _app(tmp_path: Path) -> Path:
    app = tmp_path / "UEFN Ducky.app"
    files = {
        "Contents/Info.plist": b"<plist/>",
        "Contents/MacOS/UEFN-Ducky": MACHO,
        "Contents/MacOS/UEFN-Ducky-Bridge": MACHO,
        "Contents/Frameworks/objc/_objc.cpython-312-darwin.so": MACHO,
        "Contents/Frameworks/libpython3.12.dylib": MACHO,
        "Contents/Resources/tools/ffmpeg/ffmpeg": MACHO,
        "Contents/Resources/frontend/index.html": b"<html>",
    }
    for rel, data in files.items():
        (app / rel).parent.mkdir(parents=True, exist_ok=True)
        (app / rel).write_bytes(data)
    return app


def test_code_files_are_every_macho_deepest_first(tmp_path: Path) -> None:
    app = _app(tmp_path)
    rel = [p.relative_to(app).as_posix() for p in sm.code_files(app)]
    assert rel[0] == "Contents/Resources/tools/ffmpeg/ffmpeg"
    assert set(rel) == {
        "Contents/MacOS/UEFN-Ducky", "Contents/MacOS/UEFN-Ducky-Bridge", "Contents/Frameworks/libpython3.12.dylib",
        "Contents/Frameworks/objc/_objc.cpython-312-darwin.so", "Contents/Resources/tools/ffmpeg/ffmpeg",
    }
    depth = [len(Path(r).parts) for r in rel]
    assert depth == sorted(depth, reverse=True)


def test_no_secrets_is_an_adhoc_build_ending_with_the_bundle(tmp_path: Path, capsys) -> None:
    app, run = _app(tmp_path), FakeRun()
    assert sm.sign_app(app, env={}, run=run) == "adhoc"
    assert "unsigned macOS build (ad-hoc)" in capsys.readouterr().out
    assert all(c[:4] == ["codesign", "--force", "-s", "-"] and "--entitlements" in c for c in run.calls)
    assert "--deep" not in sum(run.calls, []) and "--options" not in sum(run.calls, [])
    assert run.calls[-1][-1] == str(app)
    assert len(run.calls) == len(sm.code_files(app)) + 1
    assert sm.main(["sign", str(app)], env={}, run=FakeRun()) == 0


def test_developer_id_signs_inside_out_in_a_temporary_keychain(tmp_path: Path) -> None:
    app, run = _app(tmp_path), FakeRun()
    assert sm.sign_app(app, env=SIGNED_ENV, run=run) == "developer-id"
    tools = run.tools()
    assert tools[:5] == ["security create-keychain", "security set-keychain-settings", "security unlock-keychain",
                         "security import", "security set-key-partition-list"]
    assert tools[-1] == "security delete-keychain"
    signs = [c for c in run.calls if c[:2] == ["codesign", "--force"]]
    for c in signs:
        assert c[2:5] == ["--options", "runtime", "--timestamp"] and c[c.index("-s") + 1] == "ABCDE12345"
        assert "--deep" not in c
    assert signs[-1][-1] == str(app) and len(signs) == len(sm.code_files(app)) + 1
    verify = run.calls[-2]
    assert verify[:4] == ["codesign", "--verify", "--deep", "--strict"] and verify[-1] == str(app)


def test_some_but_not_all_secrets_is_refused(tmp_path: Path) -> None:
    with pytest.raises(sm.SigningError, match="APPLE_TEAM_ID"):
        sm.sign_app(_app(tmp_path), env={k: v for k, v in SIGNED_ENV.items() if k != "APPLE_TEAM_ID"}, run=FakeRun())


def test_not_a_bundle_is_refused(tmp_path: Path) -> None:
    assert sm.main(["sign", str(tmp_path)], env={}, run=FakeRun()) == 1


def test_notarize_skips_without_credentials(tmp_path: Path, capsys) -> None:
    run = FakeRun()
    assert sm.notarize(_app(tmp_path), env={}, run=run) is False
    assert sm.notarize(_app(tmp_path), env=NOTARY_ENV, run=run) is False  # ad-hoc build: nothing to notarize
    assert run.calls == []
    assert "notarize: skipped (no Apple credentials, ad-hoc build)" in capsys.readouterr().out
    assert sm.main(["notarize", str(_app(tmp_path))], env={}, run=run) == 0


def test_notarize_submits_and_staples_app_and_dmg(tmp_path: Path) -> None:
    app, dmg, run = _app(tmp_path), tmp_path / "UEFN-Ducky-1.3.0-macos-arm64.dmg", FakeRun()
    dmg.write_bytes(b"dmg")
    assert sm.notarize(app, dmg, env={**SIGNED_ENV, **NOTARY_ENV}, run=run) is True
    assert [c[:3] for c in run.calls] == [
        ["ditto", "-c", "-k"], ["xcrun", "notarytool", "submit"], ["xcrun", "stapler", "staple"],
        ["xcrun", "notarytool", "submit"], ["xcrun", "stapler", "staple"],
    ]
    submit = run.calls[1]
    assert submit[submit.index("--key-id") + 1] == "KEY123" and submit[submit.index("--issuer") + 1] == "issuer-uuid"
    assert "--wait" in submit and submit[submit.index("--key") + 1].endswith("AuthKey_KEY123.p8")
    assert run.calls[2][-1] == str(app) and run.calls[4][-1] == str(dmg)


def test_rejected_notarization_fetches_the_log_and_fails(tmp_path: Path) -> None:
    run = FakeRun(notary="Invalid")
    with pytest.raises(sm.SigningError, match="Invalid"):
        sm.notarize(_app(tmp_path), env={**SIGNED_ENV, **NOTARY_ENV}, run=run)
    assert run.calls[-1][:4] == ["xcrun", "notarytool", "log", "sub-1"]
    assert not any(c[:2] == ["xcrun", "stapler"] for c in run.calls)
