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


def test_toolset_boot_is_not_rewritten_when_unchanged(tmp_path: Path, monkeypatch):
    # Oct 10 2026: every island sweep rewrote ducky_listener_boot.py in the Fortnite install
    # and deleted its __pycache__, about every 8 s, even though the text never changed.
    toolset_py = tmp_path / "EditorToolset" / "Content" / "Python"
    toolset_py.mkdir(parents=True)
    (toolset_py / "init_unreal.py").write_text("import unreal\n", encoding="utf-8")
    boot_text = "# boot\nprint('ducky')\n"
    monkeypatch.setattr(deploy, "editor_toolset_python_dir", lambda: toolset_py)
    monkeypatch.setattr(deploy, "_init_text", lambda: boot_text)

    deploy.install_toolset_listener_boot()
    boot = toolset_py / "ducky_listener_boot.py"
    cache = toolset_py / "__pycache__"
    cache.mkdir()
    first_mtime = boot.stat().st_mtime_ns

    for _ in range(3):
        deploy.install_toolset_listener_boot()
    assert boot.stat().st_mtime_ns == first_mtime
    assert cache.is_dir()

    boot_text = "# boot v2\nprint('ducky')\n"
    deploy.install_toolset_listener_boot()
    assert "v2" in boot.read_text(encoding="utf-8")
    assert not cache.is_dir()  # a real update still drops the stale bytecode
