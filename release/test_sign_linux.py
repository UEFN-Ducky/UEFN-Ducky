import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "release"))
sys.path.insert(0, str(ROOT / "ducky_app"))
import sign_linux as sl  # noqa: E402

# Tiny scrypt cost so the encrypted-key tests run in milliseconds (real keys use minisign's 1 GiB).
FAST = dict(opslimit=32768, memlimit=1 << 24)


def _artifact(tmp_path: Path) -> Path:
    f = tmp_path / "UEFN-Ducky-1.2.3-x86_64.AppImage"
    f.write_bytes(os.urandom(4096))
    return f


def test_sign_and_verify_with_a_throwaway_key(tmp_path: Path) -> None:
    pub, sec = sl.generate_keypair()
    f = _artifact(tmp_path)
    key_id, seed = sl.load_secret_key(sec)
    sig = sl.sign_file(f, key_id, seed)
    assert sig.name == "UEFN-Ducky-1.2.3-x86_64.AppImage.minisig"
    text = sig.read_text(encoding="utf-8")
    assert "\tfile:UEFN-Ducky-1.2.3-x86_64.AppImage\thashed" in text
    assert sl.verify_file(f, text, pub)
    # The app's own verifier (AppImage self-update) accepts it too.
    from frontend.updater import verify_minisign

    assert verify_minisign(f, text, pub) is None


def test_tampered_file_or_comment_or_other_key_fails(tmp_path: Path) -> None:
    pub, sec = sl.generate_keypair()
    other_pub, _ = sl.generate_keypair()
    f = _artifact(tmp_path)
    text = sl.sign_file(f, *sl.load_secret_key(sec)).read_text(encoding="utf-8")
    assert not sl.verify_file(f, text, other_pub)
    assert not sl.verify_file(f, text.replace("hashed", "hashed2"), pub)
    f.write_bytes(f.read_bytes() + b"x")
    assert not sl.verify_file(f, text, pub)


def test_encrypted_secret_key_needs_its_password(tmp_path: Path) -> None:
    pub, sec = sl.generate_keypair("hunter2", **FAST)
    with pytest.raises(sl.SigningError, match="MINISIGN_PASSWORD"):
        sl.load_secret_key(sec)
    with pytest.raises(sl.SigningError, match="checksum"):
        sl.load_secret_key(sec, "wrong")
    f = _artifact(tmp_path)
    text = sl.sign_file(f, *sl.load_secret_key(sec, "hunter2")).read_text(encoding="utf-8")
    assert sl.verify_file(f, text, pub)


def test_libsodium_scrypt_params_for_minisign_defaults() -> None:
    # minisign -G: opslimit 33554432, memlimit 1073741824 -> N=2^20, r=8, p=1.
    assert sl._pick_scrypt_params(33554432, 1073741824) == (1 << 20, 8, 1)
    assert sl._pick_scrypt_params(32768, 1 << 24) == (1 << 10, 8, 1)


def test_garbage_key_is_refused() -> None:
    for bad in ("", "untrusted comment: x\nnot base64!!\n", "untrusted comment: x\nRWQ=\n"):
        with pytest.raises(sl.SigningError):
            sl.load_secret_key(bad)


def test_cli_signs_from_the_environment_and_refuses_without_a_key(tmp_path: Path, capsys) -> None:
    pub, sec = sl.generate_keypair()
    a, b = _artifact(tmp_path), tmp_path / "UEFN-Ducky-1.2.3-x86_64.AppImage.zsync"
    b.write_bytes(b"zsync")
    assert sl.main([str(a), str(b)], env={}) == 1
    assert "refusing to ship unsigned" in capsys.readouterr().err
    assert not a.with_name(a.name + ".minisig").exists()
    assert sl.main([str(a), str(b)], env={"MINISIGN_SECRET_KEY": sec}) == 0
    for f in (a, b):
        assert sl.verify_file(f, f.with_name(f.name + ".minisig").read_text(encoding="utf-8"), pub)


def test_no_key_material_lives_in_the_repo() -> None:
    tracked = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True).stdout.split()
    assert not [p for p in tracked if p.endswith((".key", ".sec")) or "minisign.key" in p]


# Repo metadata is signed on the Linux release runner; Git for Windows' msys gpg can't
# start its agent from a native process, so this runs on Linux (the CI build job).
@pytest.mark.skipif(not shutil.which("gpg") or os.name == "nt", reason="needs Linux gpg")
def test_gpg_signs_repo_metadata_with_a_throwaway_key(tmp_path: Path) -> None:
    gpg = shutil.which("gpg")
    home = tmp_path / "gnupg"
    home.mkdir(mode=0o700)
    base = [gpg, "--homedir", str(home), "--batch", "--pinentry-mode", "loopback", "--passphrase", ""]
    subprocess.run(base + ["--quick-gen-key", "Ducky Test <test@example.invalid>", "ed25519", "sign", "never"],
                   check=True, capture_output=True)
    armored = subprocess.run(base + ["--armor", "--export-secret-keys"], check=True, capture_output=True,
                             text=True).stdout
    repo = tmp_path / "repo"
    repo.mkdir()
    release, repomd = repo / "Release", repo / "repomd.xml"
    release.write_text("Origin: UEFN Ducky\nSuite: stable\n", encoding="utf-8")
    repomd.write_text("<repomd/>\n", encoding="utf-8")
    out = sl.gpg_sign([release, repomd], armored)
    assert sorted(p.name for p in out) == ["InRelease", "Release.gpg", "repomd.xml.asc"]
    verify = [gpg, "--homedir", str(home), "--batch", "--verify"]
    subprocess.run(verify + [str(repo / "Release.gpg"), str(release)], check=True, capture_output=True)
    subprocess.run(verify + [str(repo / "repomd.xml.asc"), str(repomd)], check=True, capture_output=True)
    subprocess.run(verify + [str(repo / "InRelease")], check=True, capture_output=True)
    release.write_text("Origin: tampered\n", encoding="utf-8")
    assert subprocess.run(verify + [str(repo / "Release.gpg"), str(release)], capture_output=True).returncode != 0
