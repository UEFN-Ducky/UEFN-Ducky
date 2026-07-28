# UEFN-Ducky desktop plugins — no seeds

Canonical plugin sources live only at:

```
plugins/uefn-plugin-<id>/
```

This folder is **empty on purpose**. Plugins are **never** seeded into the EXE
or copied here. Install and update only via **Settings → Store**.

```bash
./release/publish_plugin.sh <id>
# or: py release/publish_plugin.py <id>
```

See `docs/publish-to-uefn-ducky-store.md`.

**Never** run `sync_seed.py`, never pack plugins into the EXE, never Install-from-file
or copy into `%LOCALAPPDATA%/UEFN-Ducky/uefn_plugins/`.
