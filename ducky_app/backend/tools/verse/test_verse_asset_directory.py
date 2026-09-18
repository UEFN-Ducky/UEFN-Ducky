"""Compiled Verse devices are under /<Project>/_Verse, not bare /_Verse."""

from backend.tools.verse.verse_diagnostics import verse_asset_directory


def test_project_mount() -> None:
    assert verse_asset_directory("ExampleProject1") == "/ExampleProject1/_Verse"
    assert verse_asset_directory("/ExampleProject1/") == "/ExampleProject1/_Verse"


def test_empty_stays_legacy() -> None:
    assert verse_asset_directory("") == "/_Verse"


def test_tools_import_skips_lsp_bridge_and_provider_sdks() -> None:
    """MCP bridge idle import must not load verse-lsp or LLM SDKs."""
    import os
    import subprocess
    import sys
    from pathlib import Path

    ducky_app = Path(__file__).resolve().parents[3]
    code = (
        "import os, sys\n"
        "os.environ['UEFN_DUCKY_MCP_BRIDGE'] = '1'\n"
        "import backend.tools\n"
        "banned = [\n"
        "    'frontend.ui_web.verse_editor.lsp.bridge',\n"
        "    'frontend.ui_web.verse_editor.api',\n"
        "    'frontend.ui_web.verse_editor.lsp.diagnostics_scan',\n"
        "    'openai', 'anthropic', 'google.genai', 'PIL', 'tiktoken',\n"
        "]\n"
        "hit = [n for n in banned if n in sys.modules]\n"
        "assert not hit, hit\n"
        "print('ok')\n"
    )
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ducky_app) + os.pathsep + env.get("PYTHONPATH", "")
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=str(ducky_app),
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "ok" in result.stdout


if __name__ == "__main__":
    test_project_mount()
    test_empty_stays_legacy()
    print("ok")
