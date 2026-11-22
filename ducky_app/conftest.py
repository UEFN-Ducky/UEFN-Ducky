"""``@pytest.mark.windows_only("why")``: skips off Windows with that reason."""

import sys

import pytest


def pytest_configure(config):
    config.addinivalue_line("markers", "windows_only(reason): test calls Windows APIs; skipped off Windows")


def pytest_collection_modifyitems(config, items):
    if sys.platform == "win32":
        return
    for item in items:
        marker = item.get_closest_marker("windows_only")
        if marker is not None:
            why = marker.args[0] if marker.args else marker.kwargs.get("reason", "Windows API")
            item.add_marker(pytest.mark.skip(reason=f"windows_only: {why}"))
