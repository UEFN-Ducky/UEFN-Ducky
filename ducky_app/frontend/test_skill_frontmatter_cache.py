"""A turn start stats each deployed SKILL.md and reads only the ones that changed."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from backend.agent.coding_agents import mcp_inject
from backend.skills.frontmatter import serialize_frontmatter
from frontend import skill_deploy


def _pack(root: Path, name: str, managed: bool = True) -> Path:
    folder = root / name
    folder.mkdir(parents=True, exist_ok=True)
    meta = {"name": name, "metadata": {"managed_by": skill_deploy.MANAGED_BY if managed else "me"}}
    (folder / "SKILL.md").write_text(serialize_frontmatter(meta, "body"), encoding="utf-8")
    return folder / "SKILL.md"


@pytest.fixture
def skills(tmp_path, monkeypatch):
    from backend.uefn_plugins.plugin_host.testing import patch_host

    monkeypatch.setattr(skill_deploy, "_MANAGED", {})
    root = tmp_path / "skills"
    for name in ("uefn", "verse", "leveldesign"):
        _pack(root, name)
    _pack(root, "mine", managed=False)
    patch_host(monkeypatch, "get_coding_agent_registration", lambda *a: {"skills_dir": str(root)})
    reads: list[str] = []
    real = Path.read_text
    monkeypatch.setattr(Path, "read_text", lambda self, *a, **k: (reads.append(self.parent.name), real(self, *a, **k))[1])
    return root, reads


def test_a_second_turn_opens_no_skill_file(skills) -> None:
    root, reads = skills
    first = mcp_inject.deployed_skill_packs("claude")
    assert first == (str(root), ["leveldesign", "uefn", "verse"])
    assert sorted(reads) == ["leveldesign", "mine", "uefn", "verse"]
    reads.clear()
    assert mcp_inject.deployed_skill_packs("claude") == first
    assert reads == []


def test_an_edited_skill_is_read_again(skills) -> None:
    root, reads = skills
    mcp_inject.deployed_skill_packs("claude")
    reads.clear()
    edited = _pack(root, "verse", managed=False)  # the person took the folder over
    st = edited.stat()
    os.utime(edited, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000_000))
    assert mcp_inject.deployed_skill_packs("claude")[1] == ["leveldesign", "uefn"]
    assert reads == ["verse"]


def test_a_deleted_skill_leaves_the_cache(skills) -> None:
    root, _ = skills
    mcp_inject.deployed_skill_packs("claude")
    gone = root / "uefn" / "SKILL.md"
    assert str(gone) in skill_deploy._MANAGED
    gone.unlink()
    assert mcp_inject.deployed_skill_packs("claude")[1] == ["leveldesign", "verse"]
    assert str(gone) not in skill_deploy._MANAGED


def test_the_cache_is_bounded(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(skill_deploy, "_MANAGED", {})
    monkeypatch.setattr(skill_deploy, "_MANAGED_MAX", 3)
    for i in range(5):
        _pack(tmp_path, f"p{i}")
        assert skill_deploy._is_managed_skill_dir(tmp_path / f"p{i}")
    assert [Path(k).parent.name for k in skill_deploy._MANAGED] == ["p2", "p3", "p4"]
