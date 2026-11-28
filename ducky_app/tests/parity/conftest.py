"""Fixtures for parity tests: ``python``, ``rust`` and ``same(method, *args, ignore=())``."""

from __future__ import annotations

import pytest

from parity_harness import PythonBackend, RustBackend, backend_exe, check_same



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
    db.reset_for_tests()
    settings.reset_for_tests()
    db.connect()
    backend = RustBackend(exe)
    yield backend
    backend.close()  # before the folder goes: it holds ducky.db open
    db.reset_for_tests()
    settings.reset_for_tests()


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
