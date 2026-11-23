"""Windows path casing must not block abs: digest opens (BuiltIn vs builtin)."""

from __future__ import annotations

from pathlib import Path

import pytest

import frontend.ui_web.project_files as pf


@pytest.mark.windows_only("Windows paths are case-insensitive; Linux paths are not")
def test_is_path_under_case_insensitive_on_windows(tmp_path: Path, monkeypatch):
    ancestor = tmp_path / "Digests" / "BuiltIn"
    ancestor.mkdir(parents=True)
    child = ancestor / "UnrealEngine" / "UnrealEngine.digest.verse"
    child.parent.mkdir(parents=True)
    child.write_text("# digest\n", encoding="utf-8")

    # Simulate abs: keys that lowercased the folder segment.
    lower_child = Path(str(child).replace("BuiltIn", "builtin"))
    assert pf._is_path_under(lower_child, ancestor)  # noqa: SLF001


@pytest.mark.windows_only("Windows paths are case-insensitive; Linux paths are not")
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


def test_a_chat_sandbox_file_opens_read_only(tmp_path: Path, monkeypatch):
    from backend.sandbox import paths as sandbox_paths

    project = tmp_path / "Island"
    (project / "Content").mkdir(parents=True)
    boxes = tmp_path / "sandboxes"
    f = boxes / "chat1" / "work" / "repo" / "_street_graph.py"
    f.parent.mkdir(parents=True)
    f.write_text("x = 1\n", encoding="utf-8")
    outside = tmp_path / "elsewhere.py"
    outside.write_text("y = 2\n", encoding="utf-8")

    monkeypatch.setattr(pf, "_project_root", lambda: project)
    monkeypatch.setattr(pf, "_workspace_folders", lambda: [])
    monkeypatch.setattr(pf, "_other_project_content_folders", lambda: [])
    monkeypatch.setattr(sandbox_paths, "root", lambda: boxes)
    pf._invalidate_workspace_folders_cache()

    encoded = "abs:" + f.as_posix()
    assert pf._resolve_file_path(encoded) == f.resolve()  # noqa: SLF001
    assert pf._is_workspace_locked_path(encoded)  # noqa: SLF001 - opens, never writable
    try:
        pf._resolve_file_path("abs:" + outside.as_posix())  # noqa: SLF001
    except ValueError as exc:
        assert "escapes workspace folders" in str(exc)
    else:
        raise AssertionError("a file outside every folder must stay closed")
