"""Fixtures for parity tests: ``python``, ``rust`` and ``same(method, *args, ignore=())``."""

from __future__ import annotations

import json

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
