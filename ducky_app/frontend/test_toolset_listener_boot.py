"""EditorToolset boot hook for ForceEnablePython + Toolsets."""

from __future__ import annotations

from pathlib import Path

from frontend import deploy


def test_install_toolset_listener_boot_hooks_init(tmp_path: Path, monkeypatch):
    toolset_py = tmp_path / "EditorToolset" / "Content" / "Python"
    toolset_py.mkdir(parents=True)
    init = toolset_py / "init_unreal.py"
    init.write_text("import unreal\n\ntoolsets._registration.register()\n", encoding="utf-8")
    boot_src = tmp_path / "init_unreal.py"
    boot_src.write_text("# boot\nprint('ducky')\n", encoding="utf-8")

    monkeypatch.setattr(deploy, "editor_toolset_python_dir", lambda: toolset_py)
    monkeypatch.setattr(deploy, "_init_text", lambda: boot_src.read_text(encoding="utf-8"))

    msg = deploy.install_toolset_listener_boot()
    assert msg is not None
    assert "Hooked" in msg or "boot ok" in msg
    boot = toolset_py / "ducky_listener_boot.py"
    assert boot.is_file()
    assert "print('ducky')" in boot.read_text(encoding="utf-8")
    text = init.read_text(encoding="utf-8")
    assert "import ducky_listener_boot" in text
    assert deploy._TOOLSET_BOOT_MARKER in text

    msg2 = deploy.install_toolset_listener_boot()
    assert msg2 is not None
    assert text.count("import ducky_listener_boot") == init.read_text(encoding="utf-8").count(
        "import ducky_listener_boot"
    )


def test_unified_spec_bundles_one_init():
    spec = Path(__file__).resolve().parents[2] / "build" / "unified.spec"
    text = spec.read_text(encoding="utf-8")
    assert 'FRONTEND / "init_unreal.py"' in text
    assert "frozen_init_unreal.py" not in text
    assert "user_init_unreal.py" not in text
