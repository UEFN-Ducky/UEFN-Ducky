"""Windows path casing must not block abs: digest opens (BuiltIn vs builtin)."""

from __future__ import annotations

from pathlib import Path

import frontend.ui_web.project_files as pf


def test_is_path_under_case_insensitive_on_windows(tmp_path: Path, monkeypatch):
    ancestor = tmp_path / "Digests" / "BuiltIn"
    ancestor.mkdir(parents=True)
    child = ancestor / "UnrealEngine" / "UnrealEngine.digest.verse"
    child.parent.mkdir(parents=True)
    child.write_text("# digest\n", encoding="utf-8")

    # Simulate abs: keys that lowercased the folder segment.
    lower_child = Path(str(child).replace("BuiltIn", "builtin"))
    assert pf._is_path_under(lower_child, ancestor)  # noqa: SLF001


def test_decode_abs_digest_survives_builtin_casing(tmp_path: Path, monkeypatch):
    project = tmp_path / "Island"
    content = project / "Content"
    content.mkdir(parents=True)
    builtin = tmp_path / "ws" / "BuiltIn" / "UnrealEngine"
    digest = builtin / "UnrealEngine.digest.verse"
    digest.parent.mkdir(parents=True)
    digest.write_text("using { /UnrealEngine.com }\n", encoding="utf-8")

    monkeypatch.setattr(pf, "_project_root", lambda: project)
    monkeypatch.setattr(
        pf,
        "_workspace_folders",
        lambda: [{"name": "/UnrealEngine.com", "path": str(builtin)}],
    )
    pf._invalidate_workspace_folders_cache()

    encoded = "abs:" + str(digest).replace("\\", "/").replace("BuiltIn", "builtin")
    # Folder on disk is BuiltIn; encoded path may say builtin — must still resolve.
    target = pf._decode_abs_path(encoded)  # noqa: SLF001
    assert target.is_file()
    assert target.name == "UnrealEngine.digest.verse"
