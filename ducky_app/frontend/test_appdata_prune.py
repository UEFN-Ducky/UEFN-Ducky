"""Boot prune: empty leftover trees, no chats stubs, plugin runtime home."""

from __future__ import annotations

from pathlib import Path

from frontend.appdata_maintenance import (
    heal_plugin_runtime_homes,
    prune_empty_leftover_dirs,
    prune_empty_project_dirs,
    retire_cloudflare_tunnel,
)


def test_prune_empty_known_project_stub(tmp_path: Path, monkeypatch) -> None:
    root = tmp_path / "UEFN-Ducky"
    stub = root / "chats" / "projects" / "Island_abcd"
    stub.mkdir(parents=True)
    (stub / "conversations").mkdir()
    monkeypatch.setattr(
        "frontend.appdata_maintenance.load_saved_projects",
        lambda: [(str(tmp_path / "Island"), 0.0)],
    )
    monkeypatch.setattr(
        "frontend.appdata_maintenance.PanelSettings.load",
        lambda: type("S", (), {"uefn_project_root": str(tmp_path / "Island")})(),
    )
    monkeypatch.setattr(
        "frontend.ui_web.chats.store.project_slug",
        lambda _path: "Island_abcd",
    )
    assert prune_empty_project_dirs(root) >= 1
    assert not stub.exists()


def test_prune_empty_leftovers_and_build_scratch(tmp_path: Path) -> None:
    root = tmp_path / "UEFN-Ducky"
    (root / "chats" / "projects" / "gone" / "conversations").mkdir(parents=True)
    (root / "pyinstaller-work" / "unified").mkdir(parents=True)
    (root / "pyinstaller-work" / "unified" / "x").write_text("n", encoding="utf-8")
    (root / "coding_agents" / "tmp").mkdir(parents=True)
    (root / "uefn_plugins").mkdir()
    assert prune_empty_leftover_dirs(root) >= 1
    assert not (root / "chats").exists()
    assert not (root / "pyinstaller-work").exists()
    assert not (root / "coding_agents" / "tmp").exists()
    assert (root / "uefn_plugins").is_dir()


def test_heal_moves_unity_mcp_under_plugin(tmp_path: Path) -> None:
    root = tmp_path / "UEFN-Ducky"
    src = root / "unity_mcp" / "bin"
    src.mkdir(parents=True)
    (src / "uv.exe").write_bytes(b"uv")
    plugin = root / "uefn_plugins" / "unity-mcp"
    plugin.mkdir(parents=True)
    (plugin / "plugin.json").write_text("{}", encoding="utf-8")
    assert heal_plugin_runtime_homes(root) == 1
    assert not (root / "unity_mcp").exists()
    assert (plugin / "runtime" / "bin" / "uv.exe").is_file()


def test_retire_cloudflare_tunnel_once(tmp_path: Path, monkeypatch) -> None:
    root = tmp_path / "UEFN-Ducky"
    (root / "bin").mkdir(parents=True)
    (root / "bin" / "cloudflared.exe").write_bytes(b"exe")
    (root / "bin" / "uv.exe").write_bytes(b"uv")
    (root / "diagnostics").mkdir()
    (root / "diagnostics" / "cloudflared.log").write_text("log", encoding="utf-8")
    killed: list[list[str]] = []
    monkeypatch.setattr("subprocess.run", lambda argv, **_kw: killed.append(argv))
    keys = {"uefn-ducky/remote_named_tunnel": "tok"}
    monkeypatch.setattr("backend.agent.secrets.get_key", keys.get)
    monkeypatch.setattr("backend.agent.secrets.clear_key", keys.pop)
    assert retire_cloudflare_tunnel(root) == 2
    assert not (root / "bin" / "cloudflared.exe").exists()
    assert not (root / "diagnostics" / "cloudflared.log").exists()
    assert (root / "bin" / "uv.exe").is_file()
    assert keys == {}
    killed.clear()
    assert retire_cloudflare_tunnel(root) == 0
    assert killed == []
