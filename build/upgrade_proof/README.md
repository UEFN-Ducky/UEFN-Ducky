# Upgrade proof (files → ducky.db)

Two scripts that prove an installed pre-database release upgrades cleanly:

1. `gen_legacy_appdata.py <scratch> <project> <old_checkout>` — runs the **old**
   code (a checkout of the last files-based release, e.g. `main` before ADR 0003)
   with `%LOCALAPPDATA%` pointed at `<scratch>`, writing settings, API keys,
   chats + folders, a real change-ledger run, file history, memory, a plan
   template and a per-project plan, plugin data, `mcp.json`, every log file,
   the models cache, a capture, the diagnostics cache and perf traces through
   the same writers the shipped app uses.
2. `verify_upgrade.py <scratch> <project>` — boots the **new** code (this
   checkout) three times against that folder, asserts every store came across
   (keys still decrypt, chats load and are searchable, ledger entries and blobs
   match, plans folded, plugin data readable…), that the AppData root holds no
   stray file after the first boot, and that `legacy/` is deleted after the
   third clean boot.

```bash
py build/upgrade_proof/gen_legacy_appdata.py %TEMP%/ducky-up/appdata %TEMP%/ducky-up/Island C:/path/to/old-checkout
py build/upgrade_proof/verify_upgrade.py   %TEMP%/ducky-up/appdata %TEMP%/ducky-up/Island
```

The frozen EXE can run the same first-boot pass by hand:
`UEFN-Ducky.exe db import` then `UEFN-Ducky.exe db stats`.

Real samples the old code produced are checked in under
`ducky_app/backend/store/fixtures/legacy/` and drive `test_upgrade_boot.py`.
