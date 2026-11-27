"""``@pytest.mark.windows_only("why")``: skips off Windows with that reason.
``@pytest.mark.pc_only("why")``: skips on macOS, where UEFN tools send the job to the account's PC."""

import os
import sys

import pytest

# No test touches the real macOS Keychain: on a CI runner its locked login keychain hung
# SecItemAdd (test_self_check), and on a Mac it would be the person's own. The secrets vault
# falls back to its key file; tests that check the Keychain path fake ``keyring`` themselves.
if sys.platform == "darwin":
    os.environ.setdefault("PYTHON_KEYRING_BACKEND", "keyring.backends.fail.Keyring")


def pytest_configure(config):
    config.addinivalue_line("markers", "windows_only(reason): test calls Windows APIs; skipped off Windows")
    config.addinivalue_line("markers", "pc_only(reason): UEFN-on-this-PC behaviour; skipped on macOS")


def pytest_collection_modifyitems(config, items):
    if sys.platform == "win32":
        return
    for item in items:
        for name in ("windows_only", "pc_only" if sys.platform == "darwin" else ""):
            marker = item.get_closest_marker(name) if name else None
            if marker is not None:
                why = marker.args[0] if marker.args else marker.kwargs.get("reason", "Windows API")
                item.add_marker(pytest.mark.skip(reason=f"{name}: {why}"))
                break
