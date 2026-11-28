"""Fixtures for parity tests: ``python``, ``rust`` and ``same(method, *args, ignore=())``."""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import pytest

from .parity_harness import PythonBackend, RustBackend, backend_exe, check_same



@pytest.fixture
def python() -> PythonBackend:
    return PythonBackend()


@pytest.fixture
def rust_store(tmp_path, monkeypatch):
    """A Rust backend on a fresh ducky.db that this process's Python store shares.

    The app data folder is a temp one for both (the backend inherits the environment),
    and Python opens the file first, as in the app: it creates the schema.
    """
    from backend.store import db
    from backend.store.repos import settings

    exe = backend_exe()
    if exe is None:
        pytest.skip("Ducky-Backend is not built: run `cargo build -p backend` in native_launcher/")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setenv("DUCKY_TESTS_ISOLATED_APPDATA", "1")
    # Neither backend looks for islands in the person's own Documents folder.
    monkeypatch.setenv("UEFN_DUCKY_DOCUMENTS_DIR", str(tmp_path / "Documents"))
    db.reset_for_tests()
    settings.reset_for_tests()
    db.connect()
    backend = RustBackend(exe)
    yield backend
    backend.close()  # before the folder goes: it holds ducky.db open
    db.reset_for_tests()
    settings.reset_for_tests()


_SENT = ("authorization", "origin", "accept", "user-agent", "content-type", "cookie", "x-csrf-token")


class FakeSite:
    """The site, on this PC: answers every call with ``answer`` (status, headers, body), or
    with its path's own answer (or answers, one per call), and keeps what it was sent."""

    def __init__(self) -> None:
        self.answer: tuple[int, dict[str, Any], bytes] = (200, {}, b"{}")
        self.paths: dict[str, Any] = {}
        self.calls: list[dict[str, Any]] = []
        self.hold = threading.Event()  # cleared: a request waits before it is answered
        self.hold.set()
        fake = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def do_POST(self) -> None:  # noqa: N802 - the server's name for it
                raw = self.rfile.read(int(self.headers.get("content-length") or 0))
                sent = {name: self.headers.get(name) for name in _SENT if self.headers.get(name) is not None}
                call = {"path": self.path, "headers": sent, "body": json.loads(raw or b"null")}
                fake.calls.append(call if self.command == "POST" else {"method": self.command, **call})
                fake.hold.wait(30)
                answer = fake.paths.get(self.path, fake.answer)
                if isinstance(answer, list):  # one per call, the last one from then on
                    answer = answer.pop(0) if len(answer) > 1 else answer[0]
                status, headers, body = answer
                self.send_response(status)
                for name, values in {"Content-Type": "application/json", **headers}.items():
                    for value in values if isinstance(values, list) else [values]:  # a list: the header once per value
                        self.send_header(name, value)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            do_GET = do_DELETE = do_POST  # noqa: N815

            def log_message(self, *args: Any) -> None:
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def says(self, body: Any, status: int = 200, path: str = "", **headers: Any) -> None:
        """The answer to every call, or to ``path`` only."""
        raw = body if isinstance(body, bytes) else body.encode() if isinstance(body, str) else json.dumps(body).encode()
        answer = (status, {name.replace("_", "-"): value for name, value in headers.items()}, raw)
        if path:
            self.paths[path] = answer
        else:
            self.answer = answer

    def says_in_turn(self, path: str, *bodies: Any) -> None:
        """``path``'s answers, one per call; the last one stays."""
        self.paths[path] = [(200, {}, json.dumps(body).encode()) for body in bodies]

    def close(self) -> None:
        self.hold.set()
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def fake(monkeypatch):
    """The fake site, started before the Rust backend. The app-version feed is asked there.
    Signed out, the account's calls go to it over https (which it does not speak), so that
    case never leaves this PC either."""
    fake = FakeSite()
    monkeypatch.setenv("DUCKYOS_BASE_URL", f"https://127.0.0.1:{fake.server.server_address[1]}")
    monkeypatch.setenv("DUCKY_UPDATE_BASE_URL", fake.url + "/")
    yield fake
    fake.close()


LOGIN = {"base_url": "https://uefnducky.org/", "email": " Alice@Example.com", "device_key": "device-token-1"}
ADK = bytes(range(100, 132))  # the account data key the "server" hands out


def _forget_login() -> None:
    """What this process remembers of the login: as if it had just started."""
    from backend.agent import secrets
    from backend.uefn_plugins import data_crypto, scopes
    from frontend import duckyos_account

    secrets.clear_memory_cache()
    secrets._FRESH.clear()
    secrets.forget_login_stamp()
    duckyos_account.forget_account_key()
    scopes.forget_claims()
    data_crypto.reset_for_tests()


@pytest.fixture
def login(rust_store, monkeypatch):
    """Signed out to start with; ``login(blob)`` stores a login row as the app does and
    returns the account id Python gives it. The server's data key is ``ADK``."""
    from backend.agent import secrets
    from backend.store.repos.secrets import LOGIN_NAME
    from backend.uefn_plugins import data_crypto, scopes
    from frontend import duckyos_account

    def sign_in(blob: dict) -> str:
        secrets.set_key(LOGIN_NAME, json.dumps(blob))
        secrets.forget_login_stamp()
        duckyos_account.forget_account_key()
        return scopes.account_id()

    _forget_login()
    monkeypatch.setattr(data_crypto, "_fetch_adk", lambda: ADK)
    yield sign_in
    _forget_login()


@pytest.fixture(params=["signed-out", "signed-in"])
def account(request, login) -> str:
    """The account both backends act as: ``_local``, or a signed-in one whose data key
    Python has fetched and cached (so Rust has it too)."""
    from backend.uefn_plugins import data_crypto

    if request.param == "signed-out":
        return "_local"
    account = login(LOGIN)
    assert data_crypto.adk(account) == ADK
    return account


@pytest.fixture(scope="session")
def rust():
    exe = backend_exe()
    if exe is None:
        pytest.skip("Ducky-Backend is not built: run `cargo build -p backend` in native_launcher/")
    backend = RustBackend(exe)
    yield backend
    backend.close()


@pytest.fixture
def same(python, rust):
    return lambda method, *args, ignore=(): check_same(python, rust, method, *args, ignore=ignore)
