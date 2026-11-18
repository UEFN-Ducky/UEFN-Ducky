"""Frontend state files: same bytes as before, unique temp names, no write lost to a race."""

from __future__ import annotations

import json
import subprocess
import sys
import threading
from pathlib import Path

import impacted_tests
from frontend import boot_report, opencode_mcp
from frontend.ui_web import browser_vault, panel_assets, plugin_host_api, provider_usage_log
from frontend.ui_web.verse_editor.lsp import diagnostics_cache

_APP_DIR = Path(__file__).resolve().parents[1]


def _names(folder: Path) -> list[str]:
    return sorted(p.name for p in folder.iterdir())


def test_opencode_config_bytes_match_old_code(tmp_path: Path) -> None:
    path = tmp_path / "oc" / "opencode.json"
    assert opencode_mcp.merge_opencode_config(path, {"command": "duck", "args": ["--mcp"]})
    expected = {"$schema": "https://opencode.ai/config.json",
                "mcp": {"uefn": opencode_mcp.render_opencode_block({"command": "duck", "args": ["--mcp"]})}}
    assert path.read_bytes() == (json.dumps(expected, indent=2) + "\n").encode()
    assert _names(path.parent) == ["opencode.json"]


def test_browser_local_state_bytes_match_old_code(tmp_path: Path) -> None:
    browser_vault._write_state(tmp_path, {"a": 1, "b": [2]})
    folder = tmp_path / "EBWebView"
    assert (folder / "Local State").read_bytes() == b'{"a":1,"b":[2]}'
    assert _names(folder) == ["Local State"]


def test_panel_stamp_bytes_match_old_code(tmp_path: Path, monkeypatch) -> None:
    path = tmp_path / "cache" / "panel-dist.stamp.json"
    monkeypatch.setattr(panel_assets, "_stamp_path", lambda: path)
    panel_assets._write_stamp({"root": "r", "entries": 3})
    assert path.read_bytes() == json.dumps({"root": "r", "entries": 3}).encode()


def test_plugin_prefs_bytes_match_old_code(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(plugin_host_api, "_use_db", lambda: False)
    monkeypatch.setattr(plugin_host_api, "prefs_dir", lambda: tmp_path / "prefs")
    plugin_host_api.prefs_all_set({"demo": {"on": True}})
    raw = (tmp_path / "prefs" / "all.json").read_bytes()
    assert raw == (json.dumps({"demo": {"on": True}}, ensure_ascii=False, indent=0) + "\n").encode()


def test_two_writers_never_collide(tmp_path: Path, monkeypatch) -> None:
    """The window and a coding agent's tool server write the same plugin cache file."""
    monkeypatch.setattr(plugin_host_api, "_use_db", lambda: False)
    monkeypatch.setattr(plugin_host_api, "cache_dir", lambda _pid: tmp_path / "cache")
    target = tmp_path / "cache" / "board.json"
    plugin_host_api.cache_set("demo", "board", {"turn": 0})
    other = subprocess.Popen(
        [sys.executable, "-c",
         "import sys; from frontend.atomic_json import write_text_atomic\n"
         "for i in range(150): write_text_atomic(sys.argv[1], '{\"turn\": %d}' % i)",
         str(target)],
        cwd=str(_APP_DIR),
    )
    try:
        while other.poll() is None:
            plugin_host_api.cache_set("demo", "board", {"turn": "x"})
    finally:
        other.wait(timeout=60)
    plugin_host_api.cache_set("demo", "board", {"turn": "x"})
    assert other.returncode == 0
    assert plugin_host_api.cache_get("demo", "board") == {"turn": "x"}
    assert _names(target.parent) == ["board.json"]


def test_usage_log_bytes_match_old_code(tmp_path: Path) -> None:
    path = tmp_path / "provider_usage.jsonl"
    rows = [{"ts": 1.0, "model": "é"}, {"ts": 2.0}]
    provider_usage_log._write_entries(path, rows)
    assert path.read_bytes() == "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows).encode()


def test_diagnostics_cache_bytes_match_old_code(tmp_path: Path, monkeypatch) -> None:
    path = tmp_path / "slug" / diagnostics_cache.CACHE_FILE_NAME
    monkeypatch.setattr(diagnostics_cache, "_use_db", lambda: False)
    monkeypatch.setattr(diagnostics_cache, "_disk_path", lambda _root: path)
    diagnostics_cache._write_disk("C:/island", {"files": {"a.verse": {"n": "é"}}})
    expected = {"v": diagnostics_cache.CACHE_VERSION, "files": {"a.verse": {"n": "é"}}}
    assert path.read_bytes() == json.dumps(expected, ensure_ascii=False).encode("utf-8")


def test_impacted_tests_cache_bytes_match_old_code(tmp_path: Path) -> None:
    path = tmp_path / "c" / "graph.json"
    impacted_tests._save_cache(path, {"a.py": ["k", [], False]}, {"a.py": {"c.py", "b.py"}})
    expected = {"version": impacted_tests.CACHE_VERSION, "files": {"a.py": ["k", [], False]},
                "graph": {"a.py": ["b.py", "c.py"]}}
    assert path.read_bytes() == json.dumps(expected).encode()
    assert _names(path.parent) == ["graph.json"]


def test_two_reports_written_at_once_both_succeed(tmp_path: Path) -> None:
    results: list[bool] = []

    def worker(pid: int) -> None:
        for _ in range(50):
            results.append(boot_report.mark_stopped(pid, local_appdata=tmp_path))

    threads = [threading.Thread(target=worker, args=(n,)) for n in (101, 202)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert results == [True] * 100
    folder = tmp_path / "UEFN-Ducky" / "crash"
    assert json.loads((folder / "stopped.json").read_text(encoding="utf-8"))["pid"] in (101, 202)
    assert _names(folder) == ["stopped.json"]
