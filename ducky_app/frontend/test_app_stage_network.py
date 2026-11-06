"""The update pack downloads through a link that drops: TLS handshakes cut off, bodies cut
short. Nov 3 2026: "Update 1.2.465 didn't download — <urlopen error [SSL:
UNEXPECTED_EOF_WHILE_READING] EOF occurred in violation of protocol>" after 5 tries in 5 s."""

from __future__ import annotations

import datetime
import hashlib
import ipaddress
import socket
import ssl
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from frontend import app_stage, app_versions, update_swap
from frontend.test_app_stage import _clean_versions, release  # noqa: F401  (fixtures)
from frontend.update_network import ATTEMPTS


def _cert(folder: Path) -> tuple[Path, Path]:
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID

    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "127.0.0.1")])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(minutes=5))
        .not_valid_after(now + datetime.timedelta(hours=1))
        .add_extension(x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]), critical=False)
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .sign(key, hashes.SHA256())
    )
    cert_path, key_path = folder / "cert.pem", folder / "key.pem"
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(
        key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
    )
    return cert_path, key_path


class FlakyTls:
    """An HTTPS file server whose connections do what ``plan(n)`` says:
    "eof" closes during the handshake, an int sends only that many body bytes then
    drops the connection without a TLS close, "ok" serves the request."""

    def __init__(self, folder: Path, files: dict[str, bytes], plan) -> None:
        cert, key = _cert(folder)
        self.files, self.plan = files, plan
        self.server_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        self.server_ctx.load_cert_chain(cert, key)
        self.client_ctx = ssl.create_default_context(cafile=str(cert))
        self.sock = socket.create_server(("127.0.0.1", 0))
        self.sock.settimeout(0.2)
        self.port = self.sock.getsockname()[1]
        self.url = f"https://127.0.0.1:{self.port}"
        self.requests: list[tuple[str, int, str]] = []  # (path, range start, action)
        self.connections = 0
        self._stop = threading.Event()
        threading.Thread(target=self._loop, daemon=True).start()

    def close(self) -> None:
        self._stop.set()
        self.sock.close()

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                conn, _ = self.sock.accept()
            except OSError:
                continue
            n, self.connections = self.connections, self.connections + 1
            threading.Thread(target=self._one, args=(conn, self.plan(n)), daemon=True).start()

    def _one(self, conn: socket.socket, action) -> None:
        conn.settimeout(10)
        try:
            if action == "eof":
                conn.recv(4096)  # the ClientHello, then gone
                self.requests.append(("", -1, "eof"))
                return
            tls = self.server_ctx.wrap_socket(conn, server_side=True)
            data = b""
            while b"\r\n\r\n" not in data:
                chunk = tls.recv(4096)
                if not chunk:
                    return
                data += chunk
            lines = data.split(b"\r\n\r\n", 1)[0].decode("latin-1").split("\r\n")
            path = lines[0].split()[1]
            headers = {k.strip().lower(): v.strip() for k, _, v in (h.partition(":") for h in lines[1:])}
            body = self.files[path]
            start, end = 0, len(body)
            rng = headers.get("range", "")
            if rng:
                a, _, b = rng.split("=", 1)[1].partition("-")
                start, end = int(a), (int(b) + 1 if b else len(body))
            part = body[start:end]
            self.requests.append((path, start, str(action)))
            head = [f"HTTP/1.1 {206 if rng else 200} {'Partial Content' if rng else 'OK'}",
                    f"Content-Length: {len(part)}", "Content-Type: application/octet-stream", "Connection: close"]
            if rng:
                head.append(f"Content-Range: bytes {start}-{end - 1}/{len(body)}")
            tls.sendall(("\r\n".join(head) + "\r\n\r\n").encode())
            tls.sendall(part[:action] if isinstance(action, int) else part)
            if not isinstance(action, int):
                try:
                    tls.unwrap()
                except OSError:
                    pass
        except OSError:
            pass
        finally:
            conn.close()  # an int action: dropped mid-body with no TLS close_notify


@pytest.fixture(autouse=True)
def _no_waits(monkeypatch):
    waits: list[float] = []
    monkeypatch.setattr("frontend.update_network.backoff", lambda attempt: waits.append(attempt) or 0)
    monkeypatch.setattr("frontend.update_network.retry_delay", lambda attempt: 0)
    return waits


@pytest.fixture()
def tls_server(tmp_path, monkeypatch):
    made: list[FlakyTls] = []

    def make(files: dict[str, bytes], plan) -> FlakyTls:
        server = FlakyTls(tmp_path, files, plan)
        # urlopen as the app makes it, trusting only this server's certificate.
        opener = urllib.request.build_opener(urllib.request.HTTPSHandler(context=server.client_ctx))
        monkeypatch.setattr(urllib.request, "_opener", opener)
        made.append(server)
        return server

    yield make
    for server in made:
        server.close()


BODY = bytes(range(256)) * 4096  # 1 MB


def test_handshake_eof_is_the_error_people_saw_and_is_retried(tls_server, _no_waits) -> None:
    # Two handshakes cut off like Nov 3's toast, then the range arrives.
    server = tls_server({"/pack": BODY}, lambda n: "eof" if n < 2 else "ok")
    assert app_stage.fetch_range(server.url + "/pack", 1000, 600_000) == BODY[1000:600_000]
    assert [r[2] for r in server.requests] == ["eof", "eof", "ok"]
    assert _no_waits == [0, 1]  # backoff grows: 1 s, then 2 s


def test_a_range_cut_mid_body_continues_from_where_it_stopped(tls_server) -> None:
    cuts = {0: 200_000, 1: 150_000}
    server = tls_server({"/pack": BODY}, lambda n: cuts.get(n, "ok"))
    data = app_stage.fetch_range(server.url + "/pack", 10, 900_010)
    assert data == BODY[10:900_010]
    starts = [r[1] for r in server.requests]
    assert starts == [10, 10 + 200_000, 10 + 350_000]  # resumed, never restarted


def test_every_try_that_gets_bytes_resets_the_count(tls_server) -> None:
    # More drops than ATTEMPTS, but each one moved the download forward.
    server = tls_server({"/pack": BODY}, lambda n: 100_000 if n < ATTEMPTS + 3 else "ok")
    assert app_stage.fetch_range(server.url + "/pack", 0, len(BODY)) == BODY
    assert len(server.requests) == ATTEMPTS + 4


def test_a_link_that_never_answers_fails_cleanly_after_the_attempts(tls_server, _no_waits) -> None:
    server = tls_server({"/pack": BODY}, lambda n: "eof")
    with pytest.raises(urllib.error.URLError) as caught:
        app_stage.fetch_range(server.url + "/pack", 0, 1000)
    assert isinstance(caught.value.reason, ssl.SSLEOFError)
    assert server.connections == ATTEMPTS
    assert _no_waits == [0, 1, 2, 3]


def test_whole_file_fetch_resumes_after_a_cut(tls_server) -> None:
    server = tls_server({"/manifest": BODY}, lambda n: {0: "eof", 1: 300_000}.get(n, "ok"))
    assert app_stage.fetch(server.url + "/manifest") == BODY
    assert [r[1] for r in server.requests] == [-1, 0, 300_000]


def test_a_whole_update_stages_through_a_link_that_drops(release, tls_server, monkeypatch) -> None:
    files = {"/pack": release["pack"].read_bytes(), "/manifest": release["manifest_zip"].read_bytes()}
    # Every second connection is cut: handshakes, then bodies part way.
    server = tls_server(files, lambda n: "ok" if n % 2 else ("eof" if n % 4 == 0 else 2000))
    monkeypatch.setattr(app_stage, "MERGE_GAP", 0)
    folder = app_stage.stage(release["update"], base_url=server.url)
    assert app_versions.is_ready(folder)
    for entry in release["manifest"]["files"]:
        assert hashlib.sha256((folder / entry["path"]).read_bytes()).hexdigest() == entry["sha256"]
    assert any(r[2] == "eof" for r in server.requests) and any(r[2] == "2000" for r in server.requests)


def test_a_failed_download_says_so_plainly_and_retries_soon(release, tls_server, monkeypatch) -> None:
    server = tls_server({"/pack": b"", "/manifest": b""}, lambda n: "eof")
    monkeypatch.setattr(update_swap, "_state", {"phase": "idle", "version": "", "done": 0, "total": 0, "error": "", "failures": 0})
    monkeypatch.setattr("frontend.version_check.fetch_remote_payload", lambda: ({"update": release["update"]}, None))
    monkeypatch.setattr("frontend.version_check.update_base_url", lambda: server.url)
    monkeypatch.setattr(app_stage, "feed_update", lambda payload: payload["update"])
    assert update_swap.check_and_stage() is None
    status = update_swap.status()
    assert status["phase"] == "failed" and status["failures"] == 1
    assert "kept dropping" in status["error"] and "tries again in 5 minutes" in status["error"]
    assert "UNEXPECTED_EOF" in status["error"] or "EOF occurred" in status["error"]


def test_failure_messages() -> None:
    eof = urllib.error.URLError(ssl.SSLEOFError(8, "[SSL: UNEXPECTED_EOF_WHILE_READING] EOF occurred in violation of protocol (_ssl.c:1032)"))
    assert update_swap.failure_message(eof).startswith("The connection to the update server kept dropping.")
    assert update_swap.failure_message(app_stage.StageError("the update's signature did not check out")) == (
        "the update's signature did not check out"
    )
    cert = urllib.error.URLError(ssl.SSLCertVerificationError("certificate verify failed"))
    assert "secure connection failed" in update_swap.failure_message(cert)


def test_a_try_that_moved_forward_does_not_count_toward_setup(monkeypatch) -> None:
    update = {"version": "9.9.9", "packUrl": "/p", "manifestUrl": "/m", "manifestSha256": "a" * 64}
    monkeypatch.setattr(update_swap, "_state", {"phase": "idle", "version": "", "done": 0, "total": 0, "error": "", "failures": 0})
    monkeypatch.setattr("frontend.version_check.fetch_remote_payload", lambda: ({"update": update}, None))

    def _partway(u, base_url, progress=None):
        progress(4_000_000, 9_000_000)
        raise ConnectionResetError(10054, "forcibly closed")

    monkeypatch.setattr("frontend.app_stage.stage", _partway)
    update_swap.check_and_stage()
    update_swap.check_and_stage()
    assert update_swap.status()["failures"] == 0


@pytest.fixture()
def untrusted_server(tmp_path, monkeypatch):
    """A server this PC can't verify (like a root Windows hasn't fetched, or a wrong clock)."""
    made: list[FlakyTls] = []

    def make(files: dict[str, bytes]) -> FlakyTls:
        server = FlakyTls(tmp_path, files, lambda n: "ok")
        strict = urllib.request.build_opener(urllib.request.HTTPSHandler(context=ssl.create_default_context()))
        monkeypatch.setattr(urllib.request, "_opener", strict)
        made.append(server)
        return server

    yield make
    for server in made:
        server.close()


def test_an_unverifiable_certificate_still_downloads_hash_checked_files(untrusted_server) -> None:
    import io

    from frontend.update_network import fetch_into

    server = untrusted_server({"/pack": BODY, "/manifest": BODY})
    assert app_stage.fetch(server.url + "/manifest") == BODY
    assert app_stage.fetch_range(server.url + "/pack", 5, 5000) == BODY[5:5000]
    # Anything not checked against a hash still refuses the certificate.
    with pytest.raises(urllib.error.URLError) as caught:
        fetch_into(server.url + "/manifest", io.BytesIO())
    assert isinstance(caught.value.reason, ssl.SSLCertVerificationError)


def test_a_whole_update_stages_when_the_certificate_cant_be_verified(release, untrusted_server) -> None:
    files = {"/pack": release["pack"].read_bytes(), "/manifest": release["manifest_zip"].read_bytes()}
    server = untrusted_server(files)
    folder = app_stage.stage(release["update"], base_url=server.url)
    assert app_versions.is_ready(folder)


def test_a_tampered_update_is_still_refused_without_certificate_checks(release, untrusted_server) -> None:
    pack = bytearray(release["pack"].read_bytes())
    pack[len(pack) // 2] ^= 0xFF
    server = untrusted_server({"/pack": bytes(pack), "/manifest": release["manifest_zip"].read_bytes()})
    with pytest.raises(app_stage.StageError):
        app_stage.stage(release["update"], base_url=server.url)
