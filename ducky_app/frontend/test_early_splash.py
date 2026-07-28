"""Early splash helpers — no GUI required."""

from __future__ import annotations

import frontend.early_splash as early_splash
from frontend.open_files import try_handoff_to_running


def test_logo_png_resolves_in_dev_tree() -> None:
    path = early_splash._logo_png()
    assert path is not None
    assert path.is_file()
    assert path.name == "OnlineMCPIcon.png"


def test_handoff_defaults_are_short() -> None:
    """Stale panel.pid must not burn ~20s of connect timeouts on cold start."""
    import inspect

    sig = inspect.signature(try_handoff_to_running)
    assert sig.parameters["retries"].default <= 4
    assert float(sig.parameters["timeout_s"].default) <= 0.5
    assert float(sig.parameters["delay_s"].default) <= 0.25


if __name__ == "__main__":
    test_logo_png_resolves_in_dev_tree()
    test_handoff_defaults_are_short()
    print("ok")
