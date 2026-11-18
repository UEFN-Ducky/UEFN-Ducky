"""Release gates on dependency pins."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PINNED_GIT = re.compile(r"git\+\S+@[0-9a-f]{40}(?:\s|$)")


def test_requirements_have_no_unpinned_git_url():
    loose = []
    for name in ("requirements.txt", "requirements-dev.txt"):
        for line in (ROOT / name).read_text(encoding="utf-8").splitlines():
            code = line.split("#", 1)[0].strip()
            if "git+" in code and not PINNED_GIT.search(code):
                loose.append(f"{name}: {code}")
    assert not loose, f"git requirements need @<40-hex commit>: {loose}"
