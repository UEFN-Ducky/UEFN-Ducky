"""The release's exact commands and its test phase stay what the UEFN Ducky Release workflow runs.

The workflow (and .github/workflows/store-publish.yml) calls publish_app.py --phase test,
--phase build and --phase upload --notes-from-git, with verify_installer_panel.py
--extractor between build and upload. Each phase refuses unless the one before passed on
the same commit. The test phase runs vitest, pytest, then the native launcher's cargo test.
"""

from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path

import pytest

RELEASE = Path(__file__).resolve().parent
ROOT = RELEASE.parent


def _load(name: str):
    spec = importlib.util.spec_from_file_location(f"{name}_phases_test", RELEASE / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def pa(monkeypatch):
    monkeypatch.syspath_prepend(str(RELEASE))  # publish_app imports retry_scope by name
    return _load("publish_app")


class _Done:
    def __init__(self, code: int = 0) -> None:
        self.returncode = code


def _record_runs(pa, monkeypatch, *, cargo: str | None = "cargo", cargo_exit: int = 0) -> list[list[str]]:
    calls: list[list[str]] = []

    def fake_run(cmd, **_kw):
        cmd = [str(c) for c in cmd]
        calls.append(cmd)
        return _Done(cargo_exit if cmd[:2] == ["cargo", "test"] else 0)

    monkeypatch.setattr(pa, "uefn_open", lambda names=None: False)
    monkeypatch.setattr(pa.shutil, "which", lambda name: cargo if name == "cargo" else name)
    monkeypatch.setattr(pa.subprocess, "run", fake_run)
    monkeypatch.setattr(pa.os, "cpu_count", lambda: 8)
    import retry_scope

    monkeypatch.setattr(retry_scope, "retry_targets", lambda root: None)
    monkeypatch.setattr(retry_scope, "clear", lambda root: None)
    return calls


def _manifest(pa, tmp_path, monkeypatch) -> Path:
    manifest = tmp_path / "native_launcher" / "Cargo.toml"
    manifest.parent.mkdir()
    manifest.write_text('[workspace]\nmembers = ["app", "crates/*"]\n', encoding="utf-8")
    monkeypatch.setattr(pa, "NATIVE_MANIFEST", manifest)
    return manifest


# --- --phase test: pytest, vitest, then cargo test ---------------------------------


def test_cargo_test_runs_after_vitest_and_pytest(pa, tmp_path, monkeypatch) -> None:
    manifest = _manifest(pa, tmp_path, monkeypatch)
    calls = _record_runs(pa, monkeypatch)
    pa.run_regression_tests()
    vitest = next(i for i, c in enumerate(calls) if c[:2] == ["npm", "test"])
    pytest_run = next(i for i, c in enumerate(calls) if "pytest" in c and "-n" in c)
    cargo = next(i for i, c in enumerate(calls) if c[:2] == ["cargo", "test"])
    assert vitest < pytest_run < cargo
    assert calls[cargo] == ["cargo", "test", "--manifest-path", str(manifest)]


def test_a_cargo_failure_fails_the_test_phase(pa, tmp_path, monkeypatch) -> None:
    _manifest(pa, tmp_path, monkeypatch)
    _record_runs(pa, monkeypatch, cargo_exit=101)
    with pytest.raises(SystemExit) as exc:
        pa.run_regression_tests()
    assert "cargo test failed (exit 101)" in str(exc.value)


def test_no_cargo_is_only_a_warning(pa, tmp_path, monkeypatch, capsys) -> None:
    _manifest(pa, tmp_path, monkeypatch)
    calls = _record_runs(pa, monkeypatch, cargo=None)
    pa.run_regression_tests()
    assert not any("cargo" in c[0] for c in calls)
    assert "WARNING: cargo not found" in capsys.readouterr().out


def test_no_launcher_workspace_skips_cargo(pa, tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(pa, "NATIVE_MANIFEST", tmp_path / "native_launcher" / "Cargo.toml")
    calls = _record_runs(pa, monkeypatch)
    pa.run_regression_tests()
    assert not any(c[:2] == ["cargo", "test"] for c in calls)


def test_a_retry_run_also_runs_cargo(pa, tmp_path, monkeypatch) -> None:
    _manifest(pa, tmp_path, monkeypatch)
    calls = _record_runs(pa, monkeypatch)
    import retry_scope

    class _Retry:
        since = "abcdef1234"
        targets = ["tests/test_x.py"]
        web_changed = False

    monkeypatch.setattr(retry_scope, "retry_targets", lambda root: _Retry())
    pa.run_regression_tests()
    assert calls[-1][:2] == ["cargo", "test"]


def test_the_full_pytest_run_collects_every_test_folder(pa, tmp_path, monkeypatch) -> None:
    """pytest.ini's testpaths leaves out release/: the release run names it, so these pins run."""
    _manifest(pa, tmp_path, monkeypatch)
    calls = _record_runs(pa, monkeypatch)
    pa.run_regression_tests()
    full = next(c for c in calls if "pytest" in c and "-n" in c)
    for folder in ("ducky_app", "tests", "release"):
        assert folder in full
        assert (ROOT / folder).is_dir()
    # The launcher's Python side (frontend/native_switch.py and its tests) lives under ducky_app.
    assert full[full.index("-n") + 1] == "4"


# --- the pinned commands -------------------------------------------------------------


def test_release_steps_are_pinned(pa) -> None:
    assert pa.RELEASE_STEPS == ("--phase test", "--phase build", "--phase upload --notes-from-git")


def test_publish_app_accepts_every_pinned_step(pa, monkeypatch) -> None:
    seen: list[tuple[str, bool]] = []

    def stop(self, args=None, namespace=None):
        parsed = real_parse(self, args, namespace)
        seen.append((parsed.phase, parsed.notes_from_git))
        raise SystemExit(0)

    real_parse = pa.argparse.ArgumentParser.parse_args
    monkeypatch.setattr(pa.argparse.ArgumentParser, "parse_args", stop)
    for step in pa.RELEASE_STEPS:
        monkeypatch.setattr(sys, "argv", ["publish_app.py", *step.split()])
        with pytest.raises(SystemExit) as exc:
            pa.main()
        assert exc.value.code == 0
    assert seen == [("test", False), ("build", False), ("upload", True)]


def test_ci_runs_the_same_pinned_steps(pa) -> None:
    yml = (ROOT / ".github" / "workflows" / "store-publish.yml").read_text(encoding="utf-8")
    runs = [m.group(1).replace('"${extra[@]}"', "").strip()
            for m in re.finditer(r"release/publish_app\.py (--phase [^\n]*)", yml)]
    assert runs == list(pa.RELEASE_STEPS)


def test_verify_installer_panel_needs_extractor(monkeypatch, tmp_path) -> None:
    vip = _load("verify_installer_panel")
    monkeypatch.setattr(sys, "argv", ["verify_installer_panel.py"])
    with pytest.raises(SystemExit) as exc:
        vip.main()
    assert exc.value.code == 2  # argparse: --extractor is required
    ran: list[tuple[Path, Path]] = []
    monkeypatch.setattr(vip, "verify", lambda extractor, engine: ran.append((extractor, engine)))
    tool = tmp_path / "innoextract.exe"
    monkeypatch.setattr(sys, "argv", ["verify_installer_panel.py", "--extractor", str(tool)])
    try:
        vip.main()
    except SystemExit as stop:  # main may report success with exit 0
        assert not stop.code
    assert ran and ran[0][0] == tool
    assert ran[0][1].parts[-2:] == ("dist", "Setup-engine.exe")


# --- each phase needs the one before on the same commit ------------------------------


@pytest.fixture
def phases(pa, tmp_path, monkeypatch):
    monkeypatch.setattr(pa, "_PHASES", tmp_path / "dist" / "release-phases.json")
    head = {"sha": "a" * 40}
    monkeypatch.setattr(pa, "_head", lambda: head["sha"])
    return head


def test_build_refuses_before_test_passed(pa, phases) -> None:
    with pytest.raises(SystemExit) as exc:
        pa._require_phase("tested", "test")
    assert "--phase test has not passed" in str(exc.value)
    pa._record_phase("tested")
    assert pa._require_phase("tested", "test")["sha"] == phases["sha"]


def test_upload_refuses_before_build_passed(pa, phases) -> None:
    pa._record_phase("tested")
    with pytest.raises(SystemExit) as exc:
        pa._require_phase("built", "build")
    assert "--phase build has not passed" in str(exc.value)
    pa._record_phase("built", version="1.2.3", exe="Setup.exe")
    assert pa._require_phase("built", "build")["version"] == "1.2.3"


def test_a_new_commit_needs_every_phase_again(pa, phases) -> None:
    pa._record_phase("tested")
    pa._record_phase("built", version="1.2.3", exe="Setup.exe")
    phases["sha"] = "b" * 40
    with pytest.raises(SystemExit):
        pa._require_phase("tested", "test")
    with pytest.raises(SystemExit):
        pa._require_phase("built", "build")


def test_a_new_test_run_drops_the_old_build(pa, phases) -> None:
    pa._record_phase("tested")
    pa._record_phase("built", version="1.2.3", exe="Setup.exe")
    pa._record_phase("tested")
    with pytest.raises(SystemExit):
        pa._require_phase("built", "build")


def test_main_build_phase_refuses_without_a_test_run(pa, phases, monkeypatch) -> None:
    monkeypatch.setattr(pa, "preflight_store_signing_key", lambda: None)
    monkeypatch.setattr(pa, "preflight_signing", lambda require: None)
    monkeypatch.setattr(pa, "build_setup", lambda **kw: pytest.fail("built without a test run"))
    monkeypatch.setattr(sys, "argv", ["publish_app.py", "--phase", "build"])
    with pytest.raises(SystemExit) as exc:
        pa.main()
    assert "--phase test" in str(exc.value)


def test_main_upload_phase_refuses_without_a_build(pa, phases, monkeypatch) -> None:
    monkeypatch.setattr(pa, "commit_subjects_since_release", lambda: [])
    monkeypatch.setattr(pa, "notes_from_git", lambda: "")
    monkeypatch.setattr(pa, "upload_app_release", lambda *a: pytest.fail("uploaded without a build"))
    pa._record_phase("tested")
    monkeypatch.setattr(sys, "argv", ["publish_app.py", "--phase", "upload", "--notes-from-git"])
    with pytest.raises(SystemExit) as exc:
        pa.main()
    assert "--phase build" in str(exc.value)
