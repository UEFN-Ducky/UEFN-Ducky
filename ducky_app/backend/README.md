# Backend layout

Core MCP server for the UEFN-Ducky app. Desktop Store plugins live in the
separate **UEFN-Ducky** plugins repo under `plugins/uefn-plugin-<id>/backend/`.

## Tree

```text
ducky_app/backend/
  server.py                 # FastMCP singleton (stays at root)
  bridge/                   # Listener HTTP client, status, dynamic tools, plugin gate
  skills/                   # Skill-pack engine (was skill.py)
  memory/                   # Per-project agent memory
  panel/                    # Panel UI RPC client
  util/                     # json_util, env_compat, legacy import_compat
  agent/                    # Runner, providers, serialization, builtin toolsets
  tools/
    support/                # plugin_gate
    core/                   # Always-on: system, hints, code diagnostics
    panel/                  # Always-on: ducky_* / panel_* tools
    uefn/                   # Store-gated: actors, assets, devices, editor, …
    verse/                  # Store-gated: Verse digests, diagnostics, UMG
    world/                  # Store-gated: level design, worldgen, PCG, Fortnite
    animation/              # Store-gated: retargeting, sequencer
    modeling/               # Store-gated: modeling tools
    scene/                  # Store-gated: scene graph
    vfx/                    # Store-gated: Niagara
    tester/                 # Store-gated: Tester suite
    integrations/           # Store-gated: translation
  mcp_plugins/              # External JSON MCP servers (not desktop Store plugins)
  uefn_plugins/             # Desktop Store plugin host
  testing/                  # Device sim / Verse harness support libs
  voice/                    # Transcription / summary
```

Tests live beside the modules they cover (`test_*.py`).

## Where does a new file go?

1. **App infrastructure** (bridge, skills, agent loop) → matching package above.
2. **Always-on panel / `ducky_*` tools** → `tools/panel/` or `tools/core/` and import
   from `tools/__init__.py`.
3. **UEFN domain tools shipped in the EXE, Store-gated** →
   `tools/<domain>/…` with `@plugin_mcp_tool`; thin plugin `register()` imports
   the new module path (e.g. `import backend.tools.uefn.actors`).
4. **Feature that updates via Store without an app release** → entire domain under
   `plugins/uefn-plugin-<id>/backend/` with `api.tool()` (see Discord).
5. **Skill-only plugin** → plugin `skills/` + minimal `register()`.

## Bootstrap

- `tools/__init__.py` imports **only** always-on `core/` + `panel/` modules.
- Store plugins activate domain packages via side-effect import in `register()`.
- `util/import_compat.py` (loaded from `backend/__init__.py`) maps legacy flat
  paths such as `backend.tools.actors` → `backend.tools.uefn.actors` for older
  installed plugin zips. It does **not** eagerly register Store-gated tools.

## Do not confuse

- `uefn_plugins/` — desktop Store plugin host
- `mcp_plugins/` — external MCP server bridge
- `tools/tester/` — Tester Ducky plugin tools (not `testing/` support libs)
