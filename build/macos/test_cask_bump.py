import shutil
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import cask_bump as cb  # noqa: E402

ARM, INTEL = "a" * 64, "b" * 64


def test_shipped_cask_has_the_parts_brew_needs() -> None:
    text = cb.CASK.read_text(encoding="utf-8")
    for line in ('arch arm: "arm64", intel: "x64"',
                 'url "https://uefnducky.org/downloads/macos/UEFN-Ducky-#{version}-macos-#{arch}.dmg"',
                 'app "UEFN Ducky.app"', 'depends_on macos: ">= :ventura"', "Open Anyway"):
        assert line in text, line


def test_bump_rewrites_version_and_both_shas(tmp_path: Path, capsys) -> None:
    cask = tmp_path / "uefn-ducky.rb"
    shutil.copy(cb.CASK, cask)
    assert cb.main(["1.3.0", ARM, INTEL.upper(), "--cask", str(cask)]) == 0
    text = cask.read_text(encoding="utf-8")
    assert 'version "1.3.0"' in text
    assert f'sha256 arm:   "{ARM}",' in text and f'intel: "{INTEL}"' in text
    diff = capsys.readouterr().out
    assert '+  version "1.3.0"' in diff and '-  version "0.0.0"' in diff
    # Bumping again (next release) still finds every line.
    assert cb.main(["1.3.1", INTEL, ARM, "--cask", str(cask)]) == 0
    assert 'version "1.3.1"' in cask.read_text(encoding="utf-8")


@pytest.mark.parametrize("args", [["1.3", ARM, INTEL], ["1.3.0", "xyz", INTEL], ["1.3.0", ARM, "0" * 63]])
def test_bad_input_leaves_the_cask_alone(tmp_path: Path, args) -> None:
    cask = tmp_path / "uefn-ducky.rb"
    shutil.copy(cb.CASK, cask)
    before = cask.read_text(encoding="utf-8")
    assert cb.main([*args, "--cask", str(cask)]) == 1
    assert cask.read_text(encoding="utf-8") == before
