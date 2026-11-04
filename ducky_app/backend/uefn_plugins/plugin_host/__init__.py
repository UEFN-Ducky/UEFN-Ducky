"""The desktop plugin host, one module per job.

- ``state``: the registries every module shares (contributions, tools, factories, locks).
- ``boot``: the first load of every enabled plugin, ``ensure_plugins_loaded``, repair.
- ``loader``: loading one plugin (its plugin.json rows, then its backend's ``register()``).
- ``lifecycle``: Store enable / disable / reload, unloading, private-plugin pauses.
- ``backends``: importing a backend (source or compiled .pyd) and the .pyd quarantine.
- ``crash_guard``: skipping a plugin version that took Ducky down while loading.
- ``contributions``: normalizing plugin.json rows, icons, stripping a plugin's rows.
- ``ui_registry``: what the UI reads (``get_contributions``, references, sources).
- ``services``: TTS, media makers, Connections / Online rows, secret tests.
- ``gateways``: LLM providers, coding agents, IDE hookups.
- ``tool_registry``: plugin MCP tools, ownership, run allowlist, call slots.
- ``panel_rpc``: calls from a plugin's panels into its backend.
- ``folders``: plugins that keep declared folders, and reloads when those move.
- ``errors``: why a plugin didn't load.
- ``api``: the ``register(api)`` surface a plugin receives.
- ``testing``: for tests, swapping a name in every module that holds it.

Import each name from the module that owns it.
"""
