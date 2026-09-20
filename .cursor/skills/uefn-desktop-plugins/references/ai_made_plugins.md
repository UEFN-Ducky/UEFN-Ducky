# AI-made plugins (chat duckies)

**Chat / “make me a plugin” uses `ducky_plugin_*` only.** Do not follow the
git-clone / Store-publish checklist in this skill’s SKILL.md — that is for
humans shipping `Documents/GitHub/uefn-plugins/uefn-plugin-<id>/`.

In-app agents load this same recipe via:

`skill_read_subskill("ducky", "ai_plugins")`

Keep that pack and this file aligned. Tools write the draft; you never open
AppData folders.

## HARD — do this or you failed

1. `ducky_plugin_list` is the census (`drafts` + `installed`). Never Glob, shell,
   PowerShell, IDE-Read, or `workspace_write_file` into AppData `ai_plugins` /
   `uefn_plugins`. Empty drafts and no matching installed id → scaffold.
2. Edit **only** with `ducky_plugin_write_file` / `read_file` / `list(id=…)`.
3. Every plugin **must** `@api.tool()` every user-facing action (list / get /
   create / update / delete — not a lone ping). Panel RPC calls the **same**
   functions. A tab without matching MCP tools is incomplete.
4. Never git-clone `uefn-plugin-*`, never `publish_plugin.sh`, never
   Install-from-file, never `cp` into AppData, never edit `ducky_app/` / the EXE
   to add a tab. Never `ducky_skills_create_pack` unless they asked for a
   **skill pack**.
5. **Tabs: Appearance CSS vars only.** Style with `var(--bg)`, `var(--fg)`,
   `var(--fg-dim)`, `var(--muted)`, `var(--border)`, `var(--card)`,
   `var(--accent)`, `var(--accent-hover)`, `var(--btn-bg)`, `var(--red)`,
   `var(--green)`, `var(--font-family)`, … — apply the host snapshot on load
   (`theme.get`) and on `appearance_theme`. **Never** hardcode `#hex`, `rgb()`,
   or named theme colors. Exception: they specified a design/colors — use those
   tokens only, keep Ducky vars for the rest. **Always mention** the Appearance
   default when you start the UI or when you depart from it.
6. **Always pipeline + automation** (themes too). `contributes.automations.nodes`
   + `@api.register_pipeline_node` that calls the **same** functions as
   `@api.tool()`. Ship `automations.templates` with graph `start.chat` →
   `pipeline.agent` → your node → `pipeline.finish`. Omit `systems` so the tile
   is on both palettes.
7. **Bundled skill** `skills/<id>/SKILL.md` inside the draft (not
   `ducky_skills_*`).
8. **Mutators record changeset** (`api.changeset.record`, slot
   `{plugin_id}://{kind}/{id}/{facet}`).
9. **Tab UX (if they asked for a tab):** #5 plus `prefs.get` / `prefs.set`;
   empty + error states; `var(--border-focus)` on focus-visible. Any toggle →
   `settings.tabs` + `settings.sections`. First-enable → `contributes.walkthrough`.

## Path (no forks)

1. `ducky_plugin_list` — reuse an existing draft/id, or scaffold a new one
2. `ducky_plugin_scaffold(id, label, description)` if needed
3. `ducky_plugin_write_file` until `plugin.json` has `contributes.agent.tools`
   and `backend/__init__.py` registers the tools (+ `ui/index.html` if they
   asked for a tab)
4. `ducky_plugin_validate(id)`
5. `ducky_plugin_install(id)`
6. `ducky_store_set_enabled(id, true)` — if `needs_trust`, **stop**
7. Iterate: edit the **draft** → validate → install again

Copy-paste `plugin.json` / `register(api)` / panel RPC: load
`skill_read_subskill("ducky", "ai_plugins")` or `ducky_plugin_reference`.
