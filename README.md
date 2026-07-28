# UEFN-Ducky

**Control Unreal Editor for Fortnite (UEFN) from your AI coding assistant.**

UEFN-Ducky is an [MCP](https://modelcontextprotocol.io) server plus a desktop control
panel that lets Cursor, Claude Desktop, or Antigravity drive the Fortnite editor — place
and wire devices, edit Verse, inspect actors, manage materials, and more — over a local
HTTP bridge. It also ships an embedded agent ("Ducky") and a built-in Verse editor.

> **Not affiliated with Epic Games.** This is a community tool that automates *local*
> workflows against a UEFN install you already have. "Unreal", "UEFN", "Verse", and
> "Fortnite" are trademarks of Epic Games, Inc. See [LICENSE](LICENSE) and
> [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).

> **Platform:** Windows v1. The control-panel GUI and global MCP paths below are Windows.
> macOS is not bundled in the panel yet.

---

## How it works

```
  AI client (Cursor / Claude Desktop / Antigravity)
        │  MCP (stdio)
        ▼
  UEFN-Ducky MCP bridge  ──HTTP (127.0.0.1)──►  in-UEFN listener
   py -m frontend bridge (dev)              ducky_app/uefn_listener/launch_listener.py
   or UEFN-Ducky.exe bridge (release)         or Content/Python/init_unreal.py (auto-start)
```

The **MCP bridge** runs one of:
- `cd ducky_app && py -m frontend bridge` (dev, from a repo clone), or
- frozen `UEFN-Ducky.exe bridge` (release).

Inside UEFN you run `ducky_app/uefn_listener/launch_listener.py` once, or use **Deploy** in the panel to
install a one-file `Content/Python/init_unreal.py` for auto-start. They talk over local
HTTP only — nothing leaves your machine.

## Requirements

- **UEFN** installed (via the Epic Games Launcher), with the **Python Editor Script
  Plugin** enabled. UEFN also provides the Verse tooling used by the built-in editor —
  see [Verse editor & Epic assets](#verse-editor--epic-assets) below.
- **Python 3.10+** on your system (the `py` launcher is recommended on Windows). Python
  **3.11** is needed only to precompile the in-editor listener for a release build.
- An MCP client — [Cursor](https://cursor.com/), Claude Desktop, or Antigravity.

## Quick start (repo clone + Cursor)

1. `py -m pip install -r requirements.txt`
2. In UEFN: **Project Settings → enable Python Editor Script Plugin**, then
   **Tools → Execute Python Script → `ducky_app/uefn_listener/launch_listener.py`**.
3. Register the MCP server in your client (Cursor: `.cursor/mcp.json`, Claude Code:
   `.mcp.json` — both git-ignored here), then restart the IDE:

```json
{
  "mcpServers": {
    "uefn": {
      "command": "py",
      "args": ["ducky_app/frontend/launcher.py", "bridge", "--port", "4200"]
    }
  }
}
```

## Control panel (any folder, multiple IDEs)

One release binary — **`UEFN-Ducky.exe`** — opens the desktop app used for **Apply** and
**Deploy**, and your IDE runs the *same* file with `bridge --port …` for MCP stdio (no
second EXE).

Run `cd ducky_app && py -m frontend` from the repo (or `py ducky_app/frontend/launcher.py`), or the
frozen `dist/UEFN-Ducky.exe` after building.

- **Apply** writes your IDE's MCP config: `command` = `UEFN-Ducky.exe`,
  `args` = `["bridge", "--port", 4200]`.
- **Deploy** writes **only** `Content/Python/init_unreal.py` into your UEFN project (one
  file, never copies listener source into the project). It loads the listener from
  `%LOCALAPPDATA%\UEFN-Ducky\listener`, which UEFN-Ducky.exe overwrites with the latest
  plaintext source on every launch — so you deploy once and never redeploy when tools change.
- Settings: `%LOCALAPPDATA%\UEFN-Ducky\panel_settings.json`

## Verse editor & Epic assets

The built-in Verse editor uses the grammar, snippets, and language server that ship with
UEFN. **UEFN-Ducky bundles none of Epic's assets.** On first launch it extracts them from
your local UEFN install (`…\Epic Games\Fortnite\VSCode\Verse.vsix`) into git-ignored
paths. If UEFN can't be found, run the extractor manually or point it at your extension:

```bash
py scripts/extract_vscode_ext.py            # auto-detects your UEFN install
py scripts/extract_vscode_ext.py --extension "C:\path\to\extracted\verse"
```

## Building it yourself

No prebuilt binaries are committed here — build your own:

```bash
py -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt pyinstaller
.venv\Scripts\python build/build_exes.py          # -> dist/UEFN-Ducky.exe
```

> **Build in a clean virtualenv, not your system Python.** PyInstaller bundles
> everything it can import, so a global `site-packages` full of unrelated
> libraries silently adds tens of MB to the EXE.

The exe ships the plaintext `ducky_app/uefn_listener/` tree as data; UEFN runs it on its
embedded Python 3.11 at load time — no bytecode/compile step, any Python 3.10+ can build.

**Windows installer** (needs [Inno Setup 6](https://jrsoftware.org/isinfo.php)):

```bash
powershell -ExecutionPolicy Bypass -File release/installer/make_release_installer.ps1
```
→ `dist/UEFN-Ducky-Setup-<version>.exe`

**Portable zip:** `powershell -ExecutionPolicy Bypass -File release/portable/make_release_zip.ps1`
**Everything at once:** `powershell -ExecutionPolicy Bypass -File release/build_all.ps1 [-Zip] [-Sign]`

**Code signing** is optional and off by default; unsigned builds trigger a SmartScreen
warning on first run. Set `DUCKY_WINDOWS_PFX` + `DUCKY_WINDOWS_PFX_PASSWORD` and run
`py release/sign_windows.py dist/UEFN-Ducky-Setup-<version>.exe`.

## Tests

```bash
py -m pip install -r requirements-dev.txt
py -m pytest
```

## Contributing

Contributions welcome — see [CONTRIBUTING.md](CONTRIBUTING.md).

## License

[MIT](LICENSE) — free and open source. Use it, modify it, fork it, ship it in your own
products, commercially or not. The only requirement is that you keep the copyright
notice, so the code can't be passed off as someone else's work.

The **name and branding are not covered by the MIT license**: "UEFN-Ducky", the logo, and
the duck mascot stay with the project. Fork away — just ship it under your own name
rather than as an official UEFN-Ducky build. See [LICENSE](LICENSE) for the full text.

Not affiliated with Epic Games; UEFN and its bundled tooling are governed by Epic's own
terms — see [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
