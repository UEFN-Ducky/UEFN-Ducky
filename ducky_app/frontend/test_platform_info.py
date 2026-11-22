import sys

import pytest


@pytest.mark.windows_only("marker self-test")
def test_windows_only_marker_skips_off_windows():
    # Off Windows the conftest marker must skip this before it runs.
    assert sys.platform == "win32"
