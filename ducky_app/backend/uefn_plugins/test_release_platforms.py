"""A plugin release builds Windows x64/arm64, Linux x86_64/arm64 and macOS arm64/x64 zips and
publishes them in ONE uds_release; a failed build uploads nothing."""

from __future__ import annotations

import json
import textwrap

import pytest

from pathlib import Path

from backend.uefn_plugins import release_platforms as rp

_real_macos_builds = rp.macos_builds  # the fixture fakes rp.macos_builds; these tests run the real one

ABI = {"": "cp313-win_amd64", "linux-x86_64": "cpython-313-x86_64-linux-gnu",
       "linux-arm64": "cpython-313-aarch64-linux-gnu"}

# A plugin repo's scripts, as the S17 kit's plugins have them (build_zip with target=).
BUILD_ZIP = textwrap.dedent('''
    import json, zipfile
    from pathlib import Path
    ROOT = Path(__file__).resolve().parents[1]
    ABI = {abi!r}
    def build_compiled_zip(*, out=None, release=False, target=""):
        assert release, "a Store build bakes the license check in"
        m = json.loads((ROOT / "plugin.json").read_text())
        tag = f"-{{target}}" if target else ""
        (ROOT / "deploy").mkdir(exist_ok=True)
        dest = ROOT / "deploy" / f"{{m['id']}}-{{m['version']}}{{tag}}-compiled.ducky-plugin.zip"
        with zipfile.ZipFile(dest, "w") as zf:
            zf.writestr("plugin.json", json.dumps({{**m, "compiled": True, "python_abi": ABI[target]}}))
        return dest
''').format(abi=ABI)

RELEASE = textwrap.dedent('''
    CALLS, UPLOADS = [], []
    def _load_dotenv():
        pass
    def upload_with_ticket(base, key, path):
        UPLOADS.append(path.name)
        return f"up-{len(UPLOADS)}"
    def mcp_call(base, key, name, arguments):
        CALLS.append((name, arguments))
        return {"ok": True}
''')


@pytest.fixture
def plugin(tmp_path, monkeypatch):
    root = tmp_path / "uefn-plugin-fake"
    (root / "scripts").mkdir(parents=True)
    (root / "plugin.json").write_text(json.dumps({"id": "fake", "version": "1.2.3", "label": "Fake"}))
    (root / "scripts" / "build_zip.py").write_text(BUILD_ZIP)
    (root / "scripts" / "release.py").write_text(RELEASE)
    monkeypatch.setenv("DUCKYOS_API_KEY", "k")
    # No GitHub here: the arm64 build is faked where a test needs it.
    monkeypatch.setattr(rp, "windows_arm64_build", lambda p, hold: None)
    monkeypatch.setattr(rp, "linux_arm64_build", lambda p, hold: None)
    monkeypatch.setattr(rp, "macos_builds", lambda p, hold: [])
    return root


def fake_docker(plugin_root, fail_on=""):
    """Stands in for `docker run … python:3.13-bookworm`: runs the plugin's build for the target."""
    calls = []

    def run(cmd, check):
        calls.append(cmd)
        if cmd == rp.QEMU_SETUP:
            return
        assert cmd[:2] == ["docker", "run"] and rp.IMAGE in cmd and "DUCKY_REPO=/ducky" in cmd
        target = next(t for t in rp.LINUX_TARGETS if repr(t) in cmd[-1])
        assert cmd[cmd.index("--platform") + 1] == rp.LINUX_TARGETS[target]
        if target == fail_on:
            raise SystemExit(f"docker build {target} failed")
        rp._script(plugin_root, "build_zip").build_compiled_zip(release=True, target=target)

    return run, calls


def test_build_gives_three_platform_zips(plugin):
    run, calls = fake_docker(plugin)
    zips = rp.build(plugin, run=run)
    names = [z.name for z in zips]
    assert names == ["fake-1.2.3-windows-x64-compiled.ducky-plugin.zip",
                     "fake-1.2.3-linux-x86_64-compiled.ducky-plugin.zip",
                     "fake-1.2.3-linux-arm64-compiled.ducky-plugin.zip"]
    builds = [c for c in calls if c != rp.QEMU_SETUP]
    assert [c[c.index("--platform") + 1] for c in builds] == ["linux/amd64", "linux/arm64"]
    assert calls.index(rp.QEMU_SETUP) == 1  # arm64 emulation is set up right before its build


def test_a_linux_build_that_prunes_deploy_keeps_every_zip(plugin):
    """forge's Linux build deleted every zip in deploy/, the Windows one included, before the upload."""
    run, _ = fake_docker(plugin)

    def pruning_run(cmd, check):
        if cmd != rp.QEMU_SETUP:
            for old in (plugin / "deploy").glob("*.zip"):
                old.unlink()
        run(cmd, check)

    zips = rp.build(plugin, run=pruning_run)
    assert len(zips) == 3 and all(z.is_file() for z in zips)


def test_the_windows_build_is_told_where_the_kit_is(plugin, monkeypatch):
    """forge's build_zip looked only next to its own checkout and found no UEFN-Ducky."""
    import os

    monkeypatch.setenv("DUCKY_REPO", "x")  # recorded, so the value build() sets is undone after
    monkeypatch.delenv("DUCKY_REPO")
    run, _ = fake_docker(plugin)
    rp.build(plugin, run=run)
    assert os.environ["DUCKY_REPO"] == str(rp.DUCKY_REPO)


def test_publish_sends_three_artifacts_in_one_release(plugin, monkeypatch):
    run, _ = fake_docker(plugin)
    zips = rp.build(plugin, run=run)
    store = rp._script(plugin, "release")
    monkeypatch.setattr(rp, "_script", lambda p, name: store if name == "release" else None)
    rp.publish(plugin, zips, category="plugins", changelog="Linux builds")
    assert len(store.UPLOADS) == 3
    releases = [a for n, a in store.CALLS if n == "uds_release"]
    assert len(releases) == 1
    [args] = releases
    plats = {(a["os"], a["arch"]) for a in args["artifacts"]}
    assert plats == {("windows", "x86_64"), ("linux", "x86_64"), ("linux", "aarch64")}
    win = next(a for a in args["artifacts"] if a["os"] == "windows")
    assert args["uploadId"] == win["uploadId"] and args["publish"] is True and args["name"] == "Fake"
    assert all(a["uploadId"] and len(a["sha256"]) == 64 and a["size"] > 0 for a in args["artifacts"])
    assert {a["file"] for a in args["artifacts"]} == {"fake-windows-x86_64.zip", "fake-linux-x86_64.zip",
                                                     "fake-linux-aarch64.zip"}


def test_a_failed_linux_build_stops_before_any_upload(plugin, monkeypatch):
    run, _ = fake_docker(plugin, fail_on="linux-arm64")
    monkeypatch.setattr(rp.subprocess, "run", run)
    published = []
    monkeypatch.setattr(rp, "publish", lambda *a, **k: published.append(a))
    monkeypatch.setattr(rp, "_script", _no_commit(rp._script))
    with pytest.raises(SystemExit, match="linux-arm64"):
        rp.main([str(plugin), "--publish"])
    assert published == []


def _no_commit(real):
    class _Commit:
        @staticmethod
        def commit_and_push_before_publish(root, changelog):
            pass

    return lambda plugin, name: _Commit if name == "commit_before_store" else real(plugin, name)


def fake_arm64(plugin_root, hold):
    """Stands in for the plugin-windows-arm64.yml run: its downloaded zip."""
    m = json.loads((plugin_root / "plugin.json").read_text())
    dest = hold / f"{m['id']}-{m['version']}-windows-arm64-compiled.ducky-plugin.zip"
    import zipfile

    with zipfile.ZipFile(dest, "w") as zf:
        zf.writestr("plugin.json", json.dumps({**m, "compiled": True, "python_abi": "cp313-win_arm64"}))
    return dest


def test_windows_arm64_is_the_fourth_build_and_never_the_legacy_upload(plugin, monkeypatch):
    run, _ = fake_docker(plugin)
    zips = rp.build(plugin, run=run, arm64=fake_arm64)
    assert [z.name for z in zips][-1] == "fake-1.2.3-windows-arm64-compiled.ducky-plugin.zip"
    store = rp._script(plugin, "release")
    monkeypatch.setattr(rp, "_script", lambda p, name: store if name == "release" else None)
    rp.publish(plugin, zips, category="plugins", changelog="arm64")
    [args] = [a for n, a in store.CALLS if n == "uds_release"]
    plats = {(a["os"], a["arch"]) for a in args["artifacts"]}
    assert plats == {("windows", "x86_64"), ("windows", "aarch64"), ("linux", "x86_64"), ("linux", "aarch64")}
    x64 = next(a for a in args["artifacts"] if (a["os"], a["arch"]) == ("windows", "x86_64"))
    assert args["uploadId"] == x64["uploadId"]
    assert "fake-windows-aarch64.zip" in {a["file"] for a in args["artifacts"]}


def test_linux_arm64_from_github_skips_the_qemu_build(plugin):
    """road-network's Nuitka build under QEMU ran past 4 hours; GitHub's arm64 runner builds it."""
    run, calls = fake_docker(plugin)

    def native(plugin_root, hold):
        dest = hold / "fake-1.2.3-linux-arm64-compiled.ducky-plugin.zip"
        dest.write_bytes(b"zip")
        return dest

    zips = rp.build(plugin, run=run, linux_arm64=native)
    assert [z.name for z in zips] == ["fake-1.2.3-windows-x64-compiled.ducky-plugin.zip",
                                      "fake-1.2.3-linux-x86_64-compiled.ducky-plugin.zip",
                                      "fake-1.2.3-linux-arm64-compiled.ducky-plugin.zip"]
    assert rp.QEMU_SETUP not in calls
    assert [c[c.index("--platform") + 1] for c in calls] == ["linux/amd64"]


def test_github_builds_start_before_the_local_ones(plugin):
    """Run one after another, road-network's four builds took over an hour."""
    run, _ = fake_docker(plugin)
    started = []

    def gh(plugin_root, hold):
        started.append((plugin_root / "deploy").exists())  # deploy/ appears with the Windows build
        return None

    rp.build(plugin, run=run, arm64=gh, linux_arm64=gh)
    assert started == [False, False]


def test_a_failed_github_build_fails_the_release(plugin):
    run, _ = fake_docker(plugin)

    def broken(plugin_root, hold):
        raise SystemExit("linux-arm64 build failed")

    with pytest.raises(SystemExit, match="linux-arm64"):
        rp.build(plugin, run=run, linux_arm64=broken)


def fake_gh(monkeypatch, fail_watch=False):
    """Stands in for git + gh: a pushed plugin, a plugin-macos.yml run, and its two artifacts."""
    import zipfile

    seen: dict = {"dispatch": [], "download": []}

    def out(cmd, cwd=None):
        if cmd[:2] == ["git", "remote"]:
            return "https://github.com/UEFN-Ducky/uefn-plugin-fake.git"
        if cmd[:2] == ["git", "rev-parse"]:
            return "a" * 40
        if cmd[:3] == ["gh", "workflow", "run"]:
            seen["dispatch"].append(cmd[3])
            seen["request"] = next(a.split("=", 1)[1] for a in cmd if a.startswith("request="))
            return ""
        if cmd[:3] == ["gh", "run", "list"]:
            return json.dumps([{"databaseId": 7, "displayTitle": f"plugin macos {seen['request']}"}])
        if cmd[:3] == ["gh", "run", "download"]:
            artifact, dest = cmd[cmd.index("-n") + 1], Path(cmd[cmd.index("-D") + 1])
            seen["download"].append(artifact)
            arch = artifact.rsplit("-", 1)[1]
            abi = {"arm64": "cpython-313-darwin-arm64", "x64": "cpython-313-darwin-x86_64"}[arch]
            with zipfile.ZipFile(dest / f"fake-1.2.3-macos-{arch}-compiled.ducky-plugin.zip", "w") as zf:
                zf.writestr("plugin.json", json.dumps({"id": "fake", "version": "1.2.3", "compiled": True,
                                                       "python_abi": abi}))
            return ""
        raise AssertionError(cmd)

    monkeypatch.setattr(rp, "_out", out)
    monkeypatch.setattr(rp.time, "sleep", lambda s: None)
    monkeypatch.setattr(rp.subprocess, "run", lambda cmd, **kw: type("R", (), {"returncode": int(fail_watch)})())
    return seen


def test_one_release_is_six_zips_both_macs_from_one_run(plugin, monkeypatch):
    seen = fake_gh(monkeypatch)
    run, _ = fake_docker(plugin)
    zips = rp.build(plugin, run=run, arm64=fake_arm64, macos=_real_macos_builds)
    assert [z.name for z in zips] == [
        "fake-1.2.3-windows-x64-compiled.ducky-plugin.zip", "fake-1.2.3-linux-x86_64-compiled.ducky-plugin.zip",
        "fake-1.2.3-linux-arm64-compiled.ducky-plugin.zip", "fake-1.2.3-windows-arm64-compiled.ducky-plugin.zip",
        "fake-1.2.3-macos-arm64-compiled.ducky-plugin.zip", "fake-1.2.3-macos-x64-compiled.ducky-plugin.zip"]
    assert seen["dispatch"] == ["plugin-macos.yml"]  # one run builds both chips
    assert seen["download"] == ["plugin-macos-arm64", "plugin-macos-x64"]
    store = rp._script(plugin, "release")
    monkeypatch.setattr(rp, "_script", lambda p, name: store if name == "release" else None)
    rp.publish(plugin, zips, category="plugins", changelog="macOS")
    [args] = [a for n, a in store.CALLS if n == "uds_release"]
    assert {(a["os"], a["arch"]) for a in args["artifacts"]} == {
        ("windows", "x86_64"), ("windows", "aarch64"), ("linux", "x86_64"), ("linux", "aarch64"),
        ("macos", "aarch64"), ("macos", "x86_64")}
    assert args["uploadId"] == next(a["uploadId"] for a in args["artifacts"] if a["os"] == "windows" and a["arch"] == "x86_64")


def test_two_builds_for_one_mac_chip_are_refused(plugin, monkeypatch):
    fake_gh(monkeypatch)
    run, _ = fake_docker(plugin)
    zips = rp.build(plugin, run=run, macos=_real_macos_builds)
    twin = zips[-1].with_name("fake-1.2.3-macos-x64-again-compiled.ducky-plugin.zip")
    twin.write_bytes(zips[-1].read_bytes())
    store = rp._script(plugin, "release")
    monkeypatch.setattr(rp, "_script", lambda p, name: store if name == "release" else None)
    with pytest.raises(SystemExit, match="two builds for one platform"):
        rp.publish(plugin, [*zips, twin], category="plugins", changelog="x")
    assert not [n for n, _ in store.CALLS if n == "uds_release"]


def test_a_failed_mac_run_fails_the_release(plugin, monkeypatch):
    fake_gh(monkeypatch, fail_watch=True)
    with pytest.raises(SystemExit, match="macos build failed"):
        _real_macos_builds(plugin, plugin)


def test_github_slug_reads_https_and_ssh_remotes():
    assert rp.github_slug("https://github.com/UEFN-Ducky/uefn-plugin-vim.git") == "UEFN-Ducky/uefn-plugin-vim"
    assert rp.github_slug("git@github.com:UEFN-Ducky/uefn-plugin-vim.git") == "UEFN-Ducky/uefn-plugin-vim"
    assert rp.github_slug("https://x-access-token:t@github.com/o/r") == "o/r"
    assert rp.github_slug("https://gitlab.com/o/r.git") is None


def test_a_plugin_without_a_github_remote_builds_no_arm64(tmp_path):
    assert rp.windows_arm64_build(tmp_path, tmp_path) is None
    assert rp.linux_arm64_build(tmp_path, tmp_path) is None
    assert _real_macos_builds(tmp_path, tmp_path) == []


def test_linux_build_cmd_mounts_the_kit_read_only(tmp_path):
    cmd = rp.linux_build_cmd(tmp_path, "linux-arm64")
    assert f"{rp.DUCKY_REPO}:/ducky:ro" in cmd and "--platform" in cmd and "linux/arm64" in cmd
    assert cmd[-1].endswith("target='linux-arm64')")


def test_linux_build_installs_the_engine_pin_before_building(tmp_path):
    """Every plugin but google died on 'No module named iced_x86' in the bare build image."""
    (tmp_path / "requirements.txt").write_text("requests==2\niced-x86==1.22.0  # CPU check\n", encoding="utf-8")
    assert rp.build_pins(tmp_path) == ["iced-x86==1.22.0"]
    call = rp.linux_build_cmd(tmp_path, "linux-x86_64")[-1]
    assert call.index("'pip', 'install'") < call.index("build_compiled_zip")
    assert "iced-x86==" in call
    compile(call, "<docker python -c>", "exec")
