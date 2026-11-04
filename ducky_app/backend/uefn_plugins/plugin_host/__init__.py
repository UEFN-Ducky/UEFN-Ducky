"""The desktop plugin host, one module per job.

- ``state``: the registries every module shares (contributions, tools, factories, locks).
- ``boot``: the first load of every enabled plugin, ``ensure_plugins_loaded``, repair.
- ``loader``: loading one plugin (its plugin.json rows, then its backend's ``register()``).
- ``lifecycle``: Store enable / disable / reload, unloading, private-plugin pauses.

``backend.uefn_plugins.host`` still re-exports every name (see ``compat``).
"""
