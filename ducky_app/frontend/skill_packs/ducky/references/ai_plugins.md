---
description: "Build AI-made desktop plugins — themes, panels, MCP tools, any contribution"
metadata:
  order: 3
  label: "AI-made plugins"
  default_enabled: false
  load_condition: "User asks to create/customize a desktop plugin, theme, skin, panel, or add app functionality via plugins"
---

## AI-made desktop plugins

You (any chat duckie or IDE agent) can **author desktop plugins yourself** and
install them into this UEFN-Ducky install. Drafts are **shared per install** —
not owned by one AI. Other duckies can edit the same draft.

**Never edit core app files.** Customize only through plugin contributions.

### Where drafts live

`%LOCALAPPDATA%/UEFN-Ducky/ai_plugins/<id>/`

Installed (loaded) copy: `%LOCALAPPDATA%/UEFN-Ducky/uefn_plugins/<id>/` with
`source: ai`.

### Golden path

1. `ducky_plugin_reference` — contribution hooks + `register(api)` cheat sheet
2. `ducky_plugin_scaffold(id, label, description)` — create draft
3. `ducky_plugin_write_file` / `read_file` / `list` — edit files (path-jailed to the draft)
4. `ducky_plugin_validate(id)` — plugin.json, py_compile, bundled skills
5. `ducky_plugin_install(id)` — zip → install as `source=ai` (does **not** auto-enable)
6. `ducky_store_set_enabled(id, true)` — first enable returns `needs_trust`; the
   **user** confirms in Settings → Store (agents cannot auto-trust AI plugins)
7. Iterate: edit draft → validate → install again (same-source replace reloads live)

Uninstall: `ducky_store_remove(id, confirm=true)`.
Delete draft only: `ducky_plugin_delete_draft(id, confirm=true)`.

### What you can add (any kind of functionality)

Through `plugin.json` → `contributes` and/or `backend/register(api)`:

| Want | Contribution / API |
|------|--------------------|
| Theme / colors | `appearance.profiles` |
| CSS restyle | `appearance.css` |
| Background FX | `appearance.effects` |
| Full chrome swap | `appearance.skin` |
| Sandboxed HTML panel | `ui.panels` |
| Dock / editor / header | `dock.panels`, `editor.kinds`, `header.buttons` |
| Settings tabs | `settings.tabs` + `settings.sections` |
| Main-window script | `shell.boot` |
| Sounds | `sounds` / `hooks` |
| Verse scaffolds | `verse.templates` |
| New MCP tools | `api.tool()` in `register(api)` |
| Bundled skills | `skills/<skill-id>/SKILL.md` inside the plugin |

After install + enable, contributions and tools reload without rebuilding the EXE.

### Rules

- Id: `^[a-z][a-z0-9_-]{0,63}$`
- Cannot overwrite a Store/local plugin id (or vice versa) — uninstall first
- No secrets in drafts (`.dat` / `.env` / `.pem` / `.key` refused)
- Path jail: write/read only under the draft folder

### Skill packs (additive)

- `ducky_skills_create_pack` — new user-owned pack
- `ducky_skills_write_subskill` — add/update **user-origin** subskills
- On Store packs: **additive only** — new refs survive Store updates; overwriting
  store-origin `SKILL.md` / refs is refused

### Related manage tools (already exist)

Memories: `project_memory_*` · MCP servers: `ducky_mcp_*` · Plans: `ducky_create_plan` / `ducky_plan_*` · Store: `ducky_store_*`
