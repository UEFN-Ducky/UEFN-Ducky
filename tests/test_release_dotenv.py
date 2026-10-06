"""A release reads the .env at the main checkout, also when it runs from a worktree."""

from __future__ import annotations

import importlib.util
import os
from pathlib import Path

RELEASE = Path(__file__).resolve().parents[1] / "release"


def _load(name: str):
    spec = importlib.util.spec_from_file_location(f"{name}_dotenv_test", RELEASE / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


def _repo_with_worktree(tmp_path: Path) -> tuple[Path, Path]:
    main = tmp_path / "UEFN-Ducky-Release"
    (main / ".git" / "worktrees" / "ducky-rel").mkdir(parents=True)
    wt = tmp_path / "ducky-rel"
    wt.mkdir()
    (wt / ".git").write_text(f"gitdir: {main / '.git' / 'worktrees' / 'ducky-rel'}\n", encoding="utf-8")
    return main, wt


def test_worktree_reads_main_checkout_env(tmp_path):
    sign = _load("sign_windows")
    main, wt = _repo_with_worktree(tmp_path)
    assert sign.main_checkout(wt) == main
    assert sign.main_checkout(main) is None
    paths = sign.dotenv_paths(wt)
    assert paths[:2] == [wt / ".env", main / ".env"]
    assert tmp_path / "DuckyOS" / ".env" in paths
    assert tmp_path / "DuckyOS" / ".env" not in sign.dotenv_paths(wt, duckyos=False)


def test_blank_values_never_hide_a_later_file(tmp_path, monkeypatch):
    sign = _load("sign_windows")
    main, wt = _repo_with_worktree(tmp_path)
    for key in ("DUCKY_WINDOWS_PFX", "DUCKY_WINDOWS_PFX_PASSWORD", "DUCKY_SIGNTOOL_EXTRA", "DUCKY_SIGN_TIMESTAMP_URL"):
        monkeypatch.setenv(key, "")  # blank = unset, and restored after the test
    monkeypatch.setenv("DUCKY_SIGN_TIMESTAMP_URL", "http://already.set")
    (wt / ".env").write_text("DUCKY_WINDOWS_PFX=\n", encoding="utf-8")
    # Notepad saves UTF-8 with a BOM; the first key must still be read.
    (main / ".env").write_text(
        "\ufeffDUCKY_WINDOWS_PFX=C:\\certs\\ducky.pfx\n"
        "# a comment\n"
        'DUCKY_WINDOWS_PFX_PASSWORD="p=w d"\n'
        "DUCKY_SIGNTOOL_EXTRA=\n"
        "DUCKY_SIGN_TIMESTAMP_URL=http://from.file\n",
        encoding="utf-8",
    )
    sign.load_dotenv(sign.dotenv_paths(wt))

    assert os.environ["DUCKY_WINDOWS_PFX"] == "C:\\certs\\ducky.pfx"
    assert os.environ["DUCKY_WINDOWS_PFX_PASSWORD"] == "p=w d"
    assert os.environ["DUCKY_SIGNTOOL_EXTRA"] == ""
    assert os.environ["DUCKY_SIGN_TIMESTAMP_URL"] == "http://already.set"
    assert sign.signing_configured()


def test_publish_reads_the_same_files_without_duckyos(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(RELEASE))
    pub = _load("publish_app")
    import sign_windows as sign

    seen: list[list[Path]] = []
    monkeypatch.setattr(sign, "load_dotenv", lambda paths, keys=None: seen.append(list(paths)))
    pub._load_dotenv()
    assert seen and seen[0][0] == pub.ROOT / ".env"
    assert all(p.parent.name != "DuckyOS" for p in seen[0])
