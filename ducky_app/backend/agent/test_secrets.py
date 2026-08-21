"""Credential persist is Windows DPAPI; other OS stays in memory."""

from __future__ import annotations

import tempfile
from pathlib import Path

from backend.agent import secrets as secrets_mod


def test_non_windows_save_does_not_write_plaintext() -> None:
    secrets_mod.clear_memory_cache()
    orig_dpapi = secrets_mod._dpapi_available
    orig_path = secrets_mod.credentials_path
    with tempfile.TemporaryDirectory() as tmp:
        dest = Path(tmp) / "credentials.dat"
        secrets_mod._dpapi_available = lambda: False  # type: ignore[assignment]
        secrets_mod.credentials_path = lambda: dest  # type: ignore[assignment]
        try:
            secrets_mod.set_key("duckyos_account", '{"device_key":"dky_v1_test"}')
            assert not dest.exists()
            assert secrets_mod.get_key("duckyos_account") == '{"device_key":"dky_v1_test"}'
            secrets_mod.clear_key("duckyos_account")
        finally:
            secrets_mod._dpapi_available = orig_dpapi  # type: ignore[assignment]
            secrets_mod.credentials_path = orig_path  # type: ignore[assignment]
            secrets_mod.clear_memory_cache()


if __name__ == "__main__":
    test_non_windows_save_does_not_write_plaintext()
    print("ok")
