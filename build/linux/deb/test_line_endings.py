"""Every tracked file under build/linux is checked out with LF, Windows included
(build/linux/.gitattributes): bash, makepkg and rpmbuild choke on CRLF."""

import shutil
import subprocess
from pathlib import Path

import pytest

LINUX = Path(__file__).resolve().parents[1]
ROOT = LINUX.parents[1]


def test_no_crlf_in_build_linux():
    if not shutil.which("git") or not (ROOT / ".git").exists():
        pytest.skip("not a git checkout")
    files = subprocess.run(["git", "ls-files", "-z", "build/linux"], cwd=ROOT, capture_output=True,
                           check=True).stdout.decode().split("\0")
    files = [f for f in files if f]
    assert any(f.endswith(".sh") for f in files)
    crlf = [f for f in files if b"\r\n" in (ROOT / f).read_bytes()]
    assert crlf == []
