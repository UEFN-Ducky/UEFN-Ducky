# Contributing to UEFN-Ducky

Thanks for your interest! This is a Windows-first community project that automates local
UEFN workflows. Contributions of all sizes are welcome.

## Ground rules

- **Never commit Epic Games assets.** UEFN-Ducky ships **zero** Epic proprietary content.
  The Verse/URC grammars, snippets, themes, and language servers are extracted at runtime
  from the user's own UEFN install and are git-ignored. Do not add anything from
  `…\Epic Games\Fortnite\VSCode\` (or a decompiled `.vsix`) to the repository. See the
  ["Verse editor & Epic assets"](README.md#verse-editor--epic-assets) section of the README.
- **Keep it MIT-clean.** New dependencies must be under a permissive license (MIT, BSD,
  Apache-2.0, ISC, PSF). Add anything vendored to [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
- **No secrets.** Don't commit API keys, tokens, or machine-specific absolute paths.

## Dev setup

```bash
py -m pip install -r requirements.txt
py -m pip install -r requirements-dev.txt
cd ducky_app && py -m frontend
```

Run the bridge from a clone against a running UEFN listener — see the
[README](README.md) quick start.

## Running tests

```bash
py -m pytest
```

Please add or update tests for behavior changes — tests live next to the code they
cover (`test_*.py` beside the module).

## Project layout

| Path | What |
|---|---|
| `ducky_app/` | Application code: `frontend/` (panel UI) + `backend/` (MCP server) + `uefn_listener/` (UEFN-side HTTP listener) |
| `ducky_app/backend/` | Core MCP server: tools, agent, toolsets, skills |
| `ducky_app/frontend/` | Desktop app (Python) + web UI (`ui_web/`) |
| `ducky_app/uefn_listener/` | In-UEFN listener (runs inside UEFN via Python Editor Script Plugin) |
| `build/` | PyInstaller spec + build scripts |
| `scripts/` | Verse/Epic asset extraction utility |

## Pull requests

1. Keep changes focused; match the style of surrounding code.
2. Run `py -m pytest` and make sure the build still produces `dist/UEFN-Ducky.exe` if you
   touched packaging.
3. Describe what you changed and how you verified it.

By contributing, you agree your contributions are licensed under the project's
[MIT License](LICENSE).
