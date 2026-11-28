"""Fixtures for parity tests: ``python``, ``rust`` and ``same(method, *args, ignore=())``."""

from __future__ import annotations

import pytest

from parity_harness import PythonBackend, RustBackend, backend_exe, check_same

# ponytail: both backends see this PC's real app data. The first test of a call that reads
# or writes it gives both one temp folder (the env the Rust store reads, set here too).


@pytest.fixture
def python() -> PythonBackend:
    return PythonBackend()


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
