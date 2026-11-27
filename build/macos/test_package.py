import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import package as pk  # noqa: E402


class FakeRun:
    def __init__(self, rc: int = 0) -> None:
        self.calls: list[list[str]] = []
        self.rc = rc

    def __call__(self, cmd, **kw):
        self.calls.append([str(c) for c in cmd])
        return type("P", (), {"returncode": self.rc})()


def _app(tmp_path: Path) -> Path:
    app = tmp_path / "UEFN Ducky.app"
    (app / "Contents").mkdir(parents=True)
    (app / "Contents" / "Info.plist").write_bytes(b"<plist/>")
    return app


def test_names_per_arch() -> None:
    assert pk.artifact_names("1.3.0", "x64") == ("UEFN-Ducky-1.3.0-macos-x64.dmg", "uefn-ducky-update-1.3.0-macos-x64.zip")
    assert pk.mac_arch("arm64") == "arm64" and pk.mac_arch("x86_64") == "x64"


def test_dmg_stages_the_app_and_an_applications_link(tmp_path: Path) -> None:
    app, run = _app(tmp_path), FakeRun()
    out = pk.make_dmg(app, tmp_path / "UEFN-Ducky-1.3.0-macos-arm64.dmg", run)
    ditto, link, hdiutil = run.calls
    stage = Path(hdiutil[hdiutil.index("-srcfolder") + 1])
    assert ditto == ["ditto", str(app), str(stage / "UEFN Ducky.app")]
    assert link == ["ln", "-s", "/Applications", str(stage / "Applications")]
    assert hdiutil[:4] == ["hdiutil", "create", "-volname", "UEFN Ducky"]
    assert hdiutil[hdiutil.index("-format") + 1] == "UDZO" and "-ov" in hdiutil and hdiutil[-1] == str(out)


def test_zip_keeps_signatures_with_ditto(tmp_path: Path) -> None:
    app, run = _app(tmp_path), FakeRun()
    pk.make_zip(app, tmp_path / "x.app.zip", run)
    assert run.calls == [["ditto", "-c", "-k", "--sequesterRsrc", "--keepParent", str(app), str(tmp_path / "x.app.zip")]]


def test_cli_writes_both_names_and_fails_on_a_tool_error(tmp_path: Path) -> None:
    app, run = _app(tmp_path), FakeRun()
    assert pk.main([str(app), "1.3.0", "--arch", "x64", "--out", str(tmp_path / "dist")], run) == 0
    outs = [c[-1] for c in run.calls if c[0] in ("hdiutil",) or c[:2] == ["ditto", "-c"]]
    assert [Path(o).name for o in outs] == ["UEFN-Ducky-1.3.0-macos-x64.dmg", "uefn-ducky-update-1.3.0-macos-x64.zip"]
    assert pk.main([str(app), "1.3.0", "--out", str(tmp_path / "d")], FakeRun(rc=1)) == 1
    assert pk.main([str(tmp_path), "1.3.0"], FakeRun()) == 1
