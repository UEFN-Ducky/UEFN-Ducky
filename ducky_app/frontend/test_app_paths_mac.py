"""app_paths on macOS: data/config in Application Support, caches and logs in ~/Library."""

from pathlib import Path

from frontend import app_paths


def _mac(monkeypatch, home: Path, **env: str) -> None:
    monkeypatch.setattr(app_paths.sys, "platform", "darwin")
    monkeypatch.setattr(app_paths.Path, "home", classmethod(lambda cls: home))
    for var in ("DUCKY_TESTS_ISOLATED_APPDATA", "LOCALAPPDATA", "APPDATA", "XDG_DATA_HOME", "XDG_CACHE_HOME", "XDG_CONFIG_HOME"):
        monkeypatch.delenv(var, raising=False)
    for k, v in env.items():
        monkeypatch.setenv(k, v)


def test_mac_uses_library_folders(monkeypatch, tmp_path):
    _mac(monkeypatch, tmp_path, XDG_DATA_HOME=str(tmp_path / "xdg"), LOCALAPPDATA=str(tmp_path / "ignored"))
    support = tmp_path / "Library" / "Application Support"
    assert app_paths.local_app_data_base() == support
    assert app_paths.config_base() == support
    assert app_paths.cache_base() == tmp_path / "Library" / "Caches"
    assert app_paths.logs_dir() == tmp_path / "Library" / "Logs" / "UEFN-Ducky"
    assert app_paths.resolve_app_data_dir() / "settings.json" == support / "UEFN-Ducky" / "settings.json"


def test_mac_follows_the_test_isolation_seam(monkeypatch, tmp_path):
    _mac(monkeypatch, tmp_path / "home", LOCALAPPDATA=str(tmp_path / "iso"), DUCKY_TESTS_ISOLATED_APPDATA=str(tmp_path))
    assert app_paths.local_app_data_base() == tmp_path / "iso"
    assert app_paths.cache_base() == tmp_path / "iso"
    assert app_paths.logs_dir() == tmp_path / "iso" / "UEFN-Ducky" / "logs"


def test_windows_logs_dir_stays_in_appdata(monkeypatch, tmp_path):
    monkeypatch.setattr(app_paths.sys, "platform", "win32")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    assert app_paths.logs_dir() == tmp_path / "UEFN-Ducky" / "logs"
