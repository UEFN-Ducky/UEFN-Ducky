"""app_paths: Windows keeps %LOCALAPPDATA%; Linux follows the XDG base-directory spec."""

from pathlib import Path

from frontend import app_paths


def _env(monkeypatch, platform: str, **env: str) -> None:
    monkeypatch.setattr(app_paths.sys, "platform", platform)
    for var in ("LOCALAPPDATA", "APPDATA", "XDG_DATA_HOME", "XDG_CACHE_HOME", "XDG_CONFIG_HOME"):
        monkeypatch.delenv(var, raising=False)
    for k, v in env.items():
        monkeypatch.setenv(k, v)


def test_windows_uses_localappdata_for_data_cache_and_config(monkeypatch, tmp_path):
    local = str(tmp_path / "Local")
    _env(monkeypatch, "win32", LOCALAPPDATA=local, XDG_DATA_HOME=str(tmp_path / "xdg"))
    assert app_paths.local_app_data_base() == Path(local)
    assert app_paths.cache_base() == Path(local)
    assert app_paths.config_base() == Path(local)
    assert app_paths.resolve_app_data_dir() == Path(local) / "UEFN-Ducky"


def test_windows_falls_back_to_appdata(monkeypatch, tmp_path):
    _env(monkeypatch, "win32", APPDATA=str(tmp_path))
    assert app_paths.local_app_data_base() == tmp_path


def test_linux_uses_xdg_env(monkeypatch, tmp_path):
    _env(
        monkeypatch,
        "linux",
        LOCALAPPDATA=str(tmp_path / "ignored"),
        XDG_DATA_HOME=str(tmp_path / "data"),
        XDG_CACHE_HOME=str(tmp_path / "cache"),
        XDG_CONFIG_HOME=str(tmp_path / "config"),
    )
    assert app_paths.local_app_data_base() == tmp_path / "data"
    assert app_paths.cache_base() == tmp_path / "cache"
    assert app_paths.config_base() == tmp_path / "config"
    assert app_paths.resolve_app_data_dir() / "ducky.db" == tmp_path / "data" / "UEFN-Ducky" / "ducky.db"


def test_db_real_folder_guard_follows_the_os(monkeypatch, tmp_path):
    from backend.store import db

    monkeypatch.setattr(db.Path, "home", classmethod(lambda cls: tmp_path))
    monkeypatch.setattr(db.sys, "platform", "linux")
    assert db.installed_app_dir() == tmp_path / ".local" / "share" / "UEFN-Ducky"
    monkeypatch.setattr(db.sys, "platform", "win32")
    assert db.installed_app_dir() == tmp_path / "AppData" / "Local" / "UEFN-Ducky"


def test_linux_defaults_and_ignores_relative_xdg(monkeypatch, tmp_path):
    _env(monkeypatch, "linux", XDG_CACHE_HOME="relative/cache")
    monkeypatch.setattr(app_paths.Path, "home", classmethod(lambda cls: tmp_path))
    assert app_paths.local_app_data_base() == tmp_path / ".local" / "share"
    assert app_paths.cache_base() == tmp_path / ".cache"
    assert app_paths.config_base() == tmp_path / ".config"
