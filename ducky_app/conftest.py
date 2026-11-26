"""``@pytest.mark.windows_only("why")``: skips off Windows with that reason.
``@pytest.mark.pc_only("why")``: skips on macOS, where UEFN tools send the job to the account's PC."""

import sys

import pytest


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
