import importlib.util
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location("runtime_versions_tested", Path(__file__).with_name("runtime_versions.py"))
runtime = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runtime)


def test_stale_build_environment_is_rejected(tmp_path, monkeypatch):
    requirements = tmp_path / "requirements.txt"
    requirements.write_text("mcp[cli]==1.28.1\nanyio==4.14.2\nhttpx==0.28.1\npywebview==6.2.1\n")
    installed = {"mcp": "1.28.1", "anyio": "4.13.0", "httpx": "0.28.1", "pywebview": "6.2.1"}
    monkeypatch.setattr(runtime.importlib.metadata, "version", installed.__getitem__)
    with pytest.raises(RuntimeError, match="Refusing to package anyio"):
        runtime.validate(requirements)
    installed["anyio"] = "4.14.2"
    assert runtime.validate(requirements) == installed


def test_mac_build_checks_pyobjc_pins(tmp_path, monkeypatch):
    requirements = tmp_path / "requirements.txt"
    base = "mcp[cli]==1.0\nanyio==1.0\nhttpx==1.0\npywebview==1.0\n"
    mac = "".join(f'{n}==13.1; sys_platform == "darwin"\n' for n in runtime.CHECKED_MAC)
    requirements.write_text(base + mac)
    installed = {n: "1.0" for n in runtime.CHECKED} | {n: "13.1" for n in runtime.CHECKED_MAC}
    monkeypatch.setattr(runtime.importlib.metadata, "version", installed.__getitem__)
    monkeypatch.setattr(runtime.sys, "platform", "win32")
    assert set(runtime.validate(requirements)) == set(runtime.CHECKED)
    monkeypatch.setattr(runtime.sys, "platform", "darwin")
    assert runtime.validate(requirements) == installed
    installed["pyobjc-framework-WebKit"] = "12.0"
    with pytest.raises(RuntimeError, match="Refusing to package pyobjc-framework-WebKit"):
        runtime.validate(requirements)


def test_unpinned_runtime_is_rejected(tmp_path):
    requirements = tmp_path / "requirements.txt"
    requirements.write_text("mcp>=1.0\n")
    with pytest.raises(RuntimeError, match="exact tested version"):
        runtime.validate(requirements)
