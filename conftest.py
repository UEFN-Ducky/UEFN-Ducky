"""Make `py -m pytest` work from the repo root, and keep tests off the real AppData.

Tests import ``frontend`` / ``backend`` directly (the same layout the frozen exe
and ``cd ducky_app && py -m frontend`` use), so the ``ducky_app`` directory must
be on ``sys.path``.

Isolation (ADR 0003 test plan, rule 4): every test session runs with
``LOCALAPPDATA`` / ``APPDATA`` pointed at a temp dir, so stores that resolve
``frontend.app_paths.resolve_app_data_dir()`` (settings, chats, ledger, file
history, memory, the database) land there. A guard fails the session if the
developer's real ``%LOCALAPPDATA%/UEFN-Ducky`` changed while tests ran; before
this fixture existed, 196 ``tmp*`` chat folders had leaked into it.

Set ``DUCKY_TESTS_REAL_APPDATA=1`` to opt out (some manual smoke checks want it).
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent / "ducky_app"))

_REAL_LOCAL = os.environ.get("LOCALAPPDATA") or ""
_REAL_ROAMING = os.environ.get("APPDATA") or ""
_OPT_OUT = os.environ.get("DUCKY_TESTS_REAL_APPDATA") == "1"


# Areas the *running* app rewrites on its own timers (backups of every save,
# perf reports every 15 s, coding-agent temp configs, model refresh). A leak
# from a test lands in the stores that matter: chats, changesets, file_history,
# memory, settings, plugins, skill packs, plan templates.
_APP_NOISE_DIRS = {
    "backups", "perf", "coding_agents", "tool_captures", "workspace", "listener",
    "verse-lsp", "webview2_browser", "uefn_plugin_cache", "verse_diagnostics",
    "asset_previews", "mesh_previews", "translation_cache", "snapshots", "scratch",
}
_APP_NOISE_FILES = {
    "activity.jsonl", "errors.jsonl", "panel.log", "panel.pid", "ship_stamp.json",
    "models_cache.json", "mcp_command_manifest.json", "provider_usage.jsonl",
    "verse_error_stats.jsonl", "ui_crashes.jsonl", "agent_crashes.jsonl",
    "uefn_plugin_load_errors.jsonl", "ducky.db-wal", "ducky.db-shm",
    "workspace_dock.json", "window_bounds.json",
}


def _panel_is_running(real: Path) -> bool:
    try:
        pid = int((real / "panel.pid").read_text().strip())
        from frontend.open_files import panel_process_alive

        return bool(panel_process_alive(pid))
    except Exception:
        return False


def _tree_signature(root: Path) -> dict[str, tuple[int, int]]:
    """(size, mtime_ns) per file under *root*, minus the app's own background noise."""
    sig: dict[str, tuple[int, int]] = {}
    if not root.is_dir():
        return sig
    for dirpath, dirnames, filenames in os.walk(root):
        rel_dir = Path(dirpath).relative_to(root)
        if rel_dir.parts and rel_dir.parts[0] in _APP_NOISE_DIRS:
            dirnames[:] = []
            continue
        for name in filenames:
            if name in _APP_NOISE_FILES or ".bak." in name or name.endswith(".tmp"):
                continue
            p = Path(dirpath) / name
            try:
                st = p.stat()
            except OSError:
                continue
            sig[str(p.relative_to(root))] = (st.st_size, st.st_mtime_ns)
    return sig


def pytest_configure(config: pytest.Config) -> None:
    if _OPT_OUT:
        return
    root = Path(tempfile.mkdtemp(prefix="ducky-tests-appdata-"))
    (root / "Roaming").mkdir()
    os.environ["LOCALAPPDATA"] = str(root)
    os.environ["APPDATA"] = str(root / "Roaming")
    os.environ["DUCKY_TESTS_ISOLATED_APPDATA"] = str(root)
    config._ducky_isolated_root = root  # type: ignore[attr-defined]
    real = Path(_REAL_LOCAL) / "UEFN-Ducky" if _REAL_LOCAL else None
    config._ducky_real_root = real  # type: ignore[attr-defined]
    config._ducky_real_sig = _tree_signature(real) if real else {}  # type: ignore[attr-defined]


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    config = session.config
    real: Path | None = getattr(config, "_ducky_real_root", None)
    if _OPT_OUT or real is None:
        return
    before: dict = getattr(config, "_ducky_real_sig", {})
    after = _tree_signature(real)
    changed = sorted(k for k in set(before) | set(after) if before.get(k) != after.get(k))
    if changed:
        rep = session.config.pluginmanager.get_plugin("terminalreporter")
        msg = (
            f"the real AppData changed during the run ({real}); {len(changed)} paths, e.g. "
            + ", ".join(changed[:5])
        )
        # With the panel open the user may simply be chatting while tests run; only a
        # run with no live panel (CI, a quiet machine) can blame the tests with certainty.
        if _panel_is_running(real):
            if rep is not None:
                rep.write_line("REAL APPDATA GUARD (panel running, not failing): " + msg, yellow=True)
            return
        if rep is not None:
            rep.write_line("REAL APPDATA GUARD: tests wrote into the real AppData. " + msg, red=True)
        session.exitstatus = 3  # surfaced as an exit-code failure in CI


@pytest.fixture
def isolated_appdata(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A per-test AppData root (``%LOCALAPPDATA%``) for tests that want their own."""
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setenv("APPDATA", str(tmp_path / "Roaming"))
    (tmp_path / "Roaming").mkdir(exist_ok=True)
    try:
        from backend.store import db

        db.reset_for_tests()
    except Exception:
        pass
    return tmp_path
