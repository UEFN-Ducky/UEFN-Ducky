# AI-made plugins (in-app duckies)

In-app agents load the same content via the shipped **ducky** skill pack:

`skill_read_subskill("ducky", "ai_plugins")`

## Tools (always on the uefn-ducky MCP when builtin_ducky is enabled)

| Tool | Role |
|------|------|
| `ducky_plugin_reference` | Contribution + `register(api)` cheat sheet |
| `ducky_plugin_scaffold` | Create shared draft under `ai_plugins/<id>/` |
| `ducky_plugin_list` | List drafts or files in one draft |
| `ducky_plugin_write_file` / `read_file` | Path-jailed CRUD (no core files) |
| `ducky_plugin_validate` | Manifest + py_compile + skills |
| `ducky_plugin_install` | Zip → `uefn_plugins/` with `source=ai` |
| `ducky_plugin_delete_draft` | Remove draft only (`confirm=true`) |
| `ducky_store_set_enabled` / `remove` | Enable (user trust) / uninstall |
| `ducky_skills_create_pack` | New user skill pack |
| `ducky_skills_write_subskill` | Additive user subskills on Store packs |

## Flow

scaffold → write → validate → install → user trusts once → edit + reinstall.

Drafts are **per install, shared across all AIs**. Reload is automatic on
install when the plugin was already enabled (`reload_single_plugin`).

See also [SKILL.md](../SKILL.md) § AI-made plugins.