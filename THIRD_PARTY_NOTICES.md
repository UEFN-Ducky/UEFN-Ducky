# Third-Party Notices

UEFN-Ducky is licensed under the MIT License (see [LICENSE](LICENSE)). It bundles or
depends on the third-party components below, each under its own license. This file
also documents Epic Games components that are **not** redistributed but are loaded at
runtime from the user's own installation.

---

## Not redistributed — sourced from the user's UEFN install

UEFN-Ducky ships **no** Epic Games proprietary assets. The following are read at first
launch from the user's local Unreal Editor for Fortnite (UEFN) installation
(`…\Epic Games\Fortnite\VSCode\`) and cached into git-ignored paths:

- **Verse language support** (`Verse.vsix`) — grammar, snippets, themes, and the Verse
  language server (`verse-lsp` / `uLangServer`).
- **Unreal Revision Control** (`URC.vsix`) — the URC VS Code extension assets.

These are governed by the **Unreal® Engine End User License Agreement**
(https://www.unrealengine.com/eula), which the user accepts when installing UEFN.
UEFN-Ducky only automates local workflows against tooling the user already has; it is
not affiliated with or endorsed by Epic Games, Inc.

---

## Bundled dependencies

### @lore-vcs/sdk
- **License:** MIT
- **Copyright:** © 2026 Epic Games, Inc.
- **Used by:** `ducky_app/frontend/ui_web/urc_sidecar` (Unreal Revision Control integration)
- Vendored under `ducky_app/frontend/ui_web/urc_sidecar/vendor/lore-vcs-sdk/`. The MIT license
  text is retained in that directory's `LICENSE` file.

### Monaco Editor
- **License:** MIT (© Microsoft Corporation)
- Used by the control-panel web UI for the Verse editor.

### Python runtime and standard library
- **License:** PSF License — https://docs.python.org/3/license.html

### PyInstaller
- **License:** GPL with a bootloader exception permitting distribution of frozen apps
- https://pyinstaller.org

### Python and Node dependencies
- Runtime Python wheels are listed in `requirements.txt`; each is under its own
  license. Generate an exact inventory with `pip-licenses`.
- Node dependencies are listed in the respective `package.json` files; each is under its
  own license.

---

## Epic / UEFN trademark notice

"Unreal", "Unreal Engine", "UEFN", "Verse", and "Fortnite" are trademarks or registered
trademarks of Epic Games, Inc. End users need Unreal Editor for Fortnite (UEFN) under
Epic's terms. You are responsible for compliance with Epic's UEFN, Fortnite Creator, and
EULA terms when distributing software that targets UEFN.
