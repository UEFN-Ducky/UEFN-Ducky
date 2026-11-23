"""AppImage self-update: download, SHA-256 + minisign, keep .previous, atomic swap."""

from __future__ import annotations

import base64
import hashlib
import http.server
import threading
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

import frontend.app_versions as av
import frontend.updater as updater

A = b"\x7fELF" + b"version A" * 100
B = b"\x7fELF" + b"version B" * 100
KEYNUM = b"\x01\x02\x03\x04\x05\x06\x07\x08"


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _keypair() -> tuple[Ed25519PrivateKey, str]:
    priv = Ed25519PrivateKey.generate()
    raw = priv.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    pub = "untrusted comment: minisign public key\n" + base64.b64encode(b"Ed" + KEYNUM + raw).decode()
    return priv, pub


def _sign(priv: Ed25519PrivateKey, data: bytes) -> str:
    sig = priv.sign(hashlib.blake2b(data, digest_size=64).digest())
    trusted = "timestamp:1795000000\tfile:UEFN-Ducky.AppImage"
    global_sig = priv.sign(sig + trusted.encode())
    return (f"untrusted comment: signature\n{base64.b64encode(b'ED' + KEYNUM + sig).decode()}\n"
            f"trusted comment: {trusted}\n{base64.b64encode(global_sig).decode()}\n")


def _files(tmp_path: Path) -> tuple[Path, Path]:
    app = tmp_path / "UEFN-Ducky.AppImage"
    app.write_bytes(A)
    new = tmp_path / "UEFN-Ducky.AppImage.new"
    new.write_bytes(B)
    return app, new


def test_a_good_update_swaps_a_for_b_and_keeps_a_as_previous(tmp_path, monkeypatch) -> None:
    priv, pub = _keypair()
    monkeypatch.setattr(updater, "MINISIGN_PUBLIC_KEY", pub)
    app, new = _files(tmp_path)
    err = updater.install_appimage(app, new, sha256=_sha(B), signature=_sign(priv, B), version="9.9.9")
    assert err is None
    assert app.read_bytes() == B
    assert (tmp_path / "UEFN-Ducky.AppImage.previous").read_bytes() == A
    assert not new.exists()
    state, _ok, _failed = av.appimage_swap_paths()
    assert '"version": "9.9.9"' in state.read_text(encoding="utf-8")


def test_a_bad_signature_leaves_a_alone(tmp_path, monkeypatch) -> None:
    priv, pub = _keypair()
    monkeypatch.setattr(updater, "MINISIGN_PUBLIC_KEY", pub)
    app, new = _files(tmp_path)
    err = updater.install_appimage(app, new, sha256=_sha(B), signature=_sign(priv, A), version="9.9.9")
    assert err and "signature" in err
    assert app.read_bytes() == A and not new.exists()
    assert not (tmp_path / "UEFN-Ducky.AppImage.previous").exists()


def test_a_missing_signature_or_another_key_is_refused(tmp_path, monkeypatch) -> None:
    _priv, pub = _keypair()
    other, _ = _keypair()
    monkeypatch.setattr(updater, "MINISIGN_PUBLIC_KEY", pub)
    app, new = _files(tmp_path)
    assert updater.install_appimage(app, new, sha256=_sha(B), signature=None, version="9.9.9")
    app, new = _files(tmp_path)
    assert updater.install_appimage(app, new, sha256=_sha(B), signature=_sign(other, B), version="9.9.9")
    assert app.read_bytes() == A


def test_the_shipped_release_key_is_a_minisign_ed25519_public_key() -> None:
    raw = base64.b64decode(updater.MINISIGN_PUBLIC_KEY, validate=True)
    assert len(raw) == 42 and raw[:2] == b"Ed"


def test_a_sha_mismatch_or_a_non_elf_download_leaves_a_alone(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(updater, "MINISIGN_PUBLIC_KEY", "")
    app, new = _files(tmp_path)
    assert "SHA256" in updater.install_appimage(app, new, sha256="0" * 64, signature=None, version="9.9.9")
    new.write_bytes(b"<html>not an appimage</html>")
    err = updater.install_appimage(app, new, sha256=_sha(new.read_bytes()), signature=None, version="9.9.9")
    assert err and "AppImage" in err
    assert app.read_bytes() == A


def test_restart_to_update_downloads_swaps_and_restarts(tmp_path, monkeypatch) -> None:
    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            self.send_response(200)
            self.send_header("Content-Length", str(len(B)))
            self.end_headers()
            self.wfile.write(B)

        def log_message(self, format: str, *args: object) -> None:  # noqa: A003
            return

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    app, _new = _files(tmp_path)
    started: list[Path] = []
    exited: list[bool] = []
    monkeypatch.setenv("APPIMAGE", str(app))
    monkeypatch.setattr(updater, "MINISIGN_PUBLIC_KEY", "")
    monkeypatch.setattr(updater, "_stop_all_agents", lambda: None)
    monkeypatch.setattr(updater, "start_watchdog", lambda path, version: started.append(path))
    monkeypatch.setattr(updater, "_exit_self_after_delay", lambda: exited.append(True))
    url = f"http://127.0.0.1:{server.server_address[1]}/UEFN-Ducky-x86_64.AppImage"
    status = {"update_available": True, "install_kind": "appimage", "remote_version": "9.9.9",
              "download_url": "", "asset": {"url": url, "sha256": _sha(B), "sig_url": None}}
    try:
        result = updater.apply_linux_update(status=status)
    finally:
        server.shutdown()
    assert result == {"ok": True, "error": None, "stage": "restarting"}
    assert app.read_bytes() == B and started == [app] and exited == [True]
