---
name: uefn-desktop-plugins
description: >-
  Build and publish UEFN-Ducky desktop plugins (plugin.json, contributions,
  register(), Store uds_release). Install/update ONLY via Settings → Store —
  never Install-from-file, never hot-patch AppData. Use when creating or
  editing uefn-plugin-*, frontend/uefn_plugins/*, backend/uefn_plugins/*,
  Store plugin install/publish, or when the user asks how to develop a desktop
  plugin for their own Store.
---

# UEFN-Ducky desktop plugins

**HARD — no monorepo.** Each plugin is its own git repo. Work here:

`C:\Users\tas13\Documents\GitHub\uefn-plugins\uefn-plugin-<id>\`

Never edit or publish from `UEFN-Ducky/plugins/` (archived `desktop-plugins`
monorepo). Never ship from `%TEMP%/uefn-plugin-*-ship`. Commit and
`git push` the standalone repo; publish with
`py -3 scripts/release.py --publish` from that clone.

Desktop plugins extend the **UEFN-Ducky app** (not DuckyOS site plugins like
`plugin-discord-bot`). Runtime root:

`%LOCALAPPDATA%/UEFN-Ducky/uefn_plugins/<id>/`

Plugin Python lives only in the standalone repo (`backend/*.py`)
and `%LOCALAPPDATA%/UEFN-Ducky/uefn_plugins/<id>/`. Never drop plugin `.py`
into an island. Ducky already auto-manages `Content/Python/init_unreal.py`
(listener) — **never delete that file.**

Canonical package is the clone root (`plugin.json` at repo root).
**Never seed** into `ducky_app/frontend/uefn_plugins/` or the EXE — Store-only.

Reference Discord stub: `uefn-plugins/uefn-plugin-discord/`.

For field-level schema and pitfalls, see [reference.md](reference.md).

## Package layout

```
uefn-plugins/uefn-plugin-<id>/   # standalone git repo (not a monorepo folder)
  plugin.json
  backend/
    __init__.py                # def register(api); optional unload()
    <domain>.py                # split modules — avoid mega __init__
    tools.py                   # api.tool() when tools live in-plugin
    panel_rpc.py               # optional
    test_*.py
  listener/                    # optional Unreal Python (@register); host overlays into AppData
  ui/                          # optional Phase-2 HTML panels
  skills/<skill-id>/SKILL.md   # optional: bundled skill packs (see below)
  skills/<skill-id>/references/*.md
  assets/
  scripts/                     # build_zip / release (not zipped; never sync_seed)
  deploy/                      # built .ducky-plugin.zip (not zipped)
```

`id` must match `^[a-z][a-z0-9_-]{0,63}$`.

**App backend hierarchy (UEFN-Ducky-Release):** domain tools that still ship in
the EXE live under `ducky_app/backend/tools/<domain>/` (e.g. `uefn/`, `verse/`,
`world/`). Thin Store plugins activate them with:

```python
import backend.tools.uefn.actors  # noqa: F401
```

Full Store-owned features (Discord) keep all Python in this plugin package and
register via `api.tool()`. See Release `ducky_app/backend/README.md`.

### Bundled skills (optional)

A plugin may ship skill packs under `skills/<skill-id>/SKILL.md` (folder name =
skill id). No `contributes` entry required — presence of the folder is the
declaration. Skills are available while the plugin is **installed** (enable/
disable does not remove them). Update/uninstall of the plugin updates/removes
those skills. Skill ids must be unique: install fails if the id is already a
standalone AppData skill pack or owned by another plugin.

## Minimal plugin.json

```json
{
  "id": "hello",
  "kind": "plugin",
  "version": "1.0.1",
  "label": "Hello",
  "description": "Minimal desktop plugin",
  "min_app_version": "1.0.0",
  "default_enabled": false,
  "secret_keys": [],
  "contributes": {
    "settings.tabs": [
      { "id": "Hello", "label": "Hello", "icon": "chat", "ui": "builtin:hello-settings" }
    ],
    "dock.panels": [
      { "id": "hello-dock", "title": "Hello", "defaultSide": "left", "ui": "builtin:hello-dock" }
    ],
    "editor.kinds": [
      { "kind": "hello", "title": "Hello", "ui": "builtin:hello-dock" }
    ],
    "agent.tools": {
      "category": "hello",
      "intent_pattern": "\\bhello\\b"
    }
  },
  "backend": { "entry": "backend", "register": "register" }
}
```

## Contributions (what they actually do)

| Hook | Effect when plugin **enabled** |
|------|--------------------------------|
| `settings.tabs` | Dynamic Settings tab; `ui`: sections-only, `builtin:…`, or `panel:<id>` |
| `settings.sections` | Declarative toggles in that Settings tab via `PluginSettingsSections` |
| `dock.panels` | Dock id appears only if gated on `pluginContributesDockPanel` |
| `editor.kinds` | Claim a file kind / suffixes → `PluginFilePane` (`ui: "panel:…"`); else binary |
| `header.buttons` | Header icons; `builtin:…`, `settings:<tabId>`, or `panel:<id>` |
| `ui.panels` | Sandboxed HTML (editor tab or Settings embed via `panel:`) |
| `shell.boot` | Main-window script + `__duckyPluginHost` (prefs / llm / cache) |
| `appearance.profiles` | Editable theme profiles in Appearance (reset → plugin defaults). Host ships Default only; Light/Hacker/Galaxy Craft are Store plugins. |
| `appearance.css` | Stylesheet after ThemeProvider (chrome restyle) |
| `appearance.effects` | Background FX into `#ducky-fx-root` (Effects accordion + per-theme default) |
| `appearance.skin` | Full chrome swap into host portals (frame/header/left/right) |
| `sounds` | Audio files listed in Appearance → Sounds (`plugin:<id>:<soundId>`) |
| `hooks` | Extra hookable events for Appearance → Sounds; emit via `ducky:hook` |
| `verse.templates` | New-file Verse scaffolds (`file`/`content` or multi-file `folder`+`files[]`) |
| `agent.tools` | Optional category / intent for MCP tools registered via `api.tool()` (tool names auto-tracked) |
| `llm.providers` | Rows under Settings → LLMs → Providers; click opens a detail slide (key, coding agent, plugin options) |
| `llm.coding_agents` | Coding-agent block inside that provider’s detail slide (Claude Code, Codex, Cursor, Gemini CLI) |
| `settings.sections` with `tab: "LLMs"` | Extra toggles in that provider’s detail slide (e.g. Anthropic/OpenAI prompt-cache markers) |
| `api.register_ide_hookup(kind)` | Own IDE MCP+skills Apply (cursor / claude / antigravity); auto-applies on register; UI in that provider’s LLMs detail |
| `walkthrough` | First-enable product tour (`plugin.<id>`). Host spotlights `target` ui-ids with Next / require_click. See Translation / Discord examples. |

### `contributes.walkthrough`

Owns a per-plugin coachmark tour. Auto-starts once when the plugin is first
enabled (Store). Completions persist in `PanelSettings.walkthrough_completed`.

```json
"walkthrough": {
  "id": "translation",
  "title": "Set up Translation",
  "auto_start": "first_enable",
  "settings_tab": "Languages",
  "steps": [
    {
      "target": "settings.tab.languages",
      "title": "Languages tab",
      "body": "…",
      "advance": "require_click",
      "mode": "rect"
    },
    {
      "target": "settings.languages.add",
      "title": "Add a language",
      "body": "…",
      "advance": "next"
    }
  ]
}
```

- `target`: semantic ui-target id (tag DOM with `useUiTarget` / `targetRef` in host UI, or reuse `settings.tab.<slug>`).
- `advance`: `next` (coachmark Next) or `require_click` (user must click the highlighted control).
- `settings_tab`: opens that Settings sidebar tab before step 0.
- Reference: `plugins/uefn-plugin-translation/plugin.json`, `plugins/uefn-plugin-discord/plugin.json`.

**Phase 1 UI:** `ui: "builtin:…"` marks host-owned complex forms (e.g. Discord
Connection). Declarative `settings.sections` / `header.buttons` ship in the
plugin zip and refresh after Store update without an EXE rebuild.

**Phase 2 UI:** `ui.panels` ships `ui/index.html` (etc.) in the zip. Served at
`/plugin-ui/<id>/…`, rendered in a sandboxed iframe. Code lives in
`backend/uefn_plugins/webview.py` + `web/src/plugin-ui/` (isolated; see that
folder’s README).

## Backend `register(api)` — MCP hook

Extend the app's own MCP server (`FastMCP("uefn-ducky")` / IDE `mcpServers.uefn`)
with `api.tool()`. Tools reach the in-app agent **and** every connected IDE.
Enable/disable + per-chat opt-in are enforced inside the decorator.

```python
def register(api) -> None:
    @api.tool(intent=r"\b(sequencer|cinematic|timeline)\b")
    def sequencer_create_shot(name: str, duration: float = 5.0) -> str:
        """Create a Level Sequence shot in the live UEFN editor."""
        result = api.listener(
            "create_level_sequence",
            {"asset_name": name, "duration": duration},
        )
        return str(result)

    api.log("sequencer tools registered")
```

| API | Purpose |
|-----|---------|
| `api.tool()` / `@api.tool(name=…, intent=…)` | Register on shared FastMCP; auto-gate + track names |
| `api.register_secret_test(secret_key, fn)` | Settings → Test for a `secret` field (`fn(key) -> {ok, detail}`) |
| `api.listener(command, params=None, timeout=…)` | Drive the live UEFN editor (`send_command`) |
| `api.is_enabled()` | Store enable gate (for background work) |
| `api.log(msg)` / `api.plugin_id` | Diagnostics |

Optional `contributes.agent.tools` still sets category / intent pattern / Store
"includes tools" summary. Listing every tool name there is no longer required
when using `api.tool()`.

**Legacy (still works):** import a module that uses `@mcp.tool()` on the global
`mcp` instance and hand-gate with `is_plugin_enabled` / `uefn_agent_tools_allowed`.
Prefer `api.tool()` for new plugins.

## Secrets

- List names in `secret_keys` (metadata only).
- Values: `backend.agent.secrets.set_key(name, value)` → DPAPI `credentials.dat`.
- **Never** put tokens in the zip. `build_zip.py` refuses `.dat`/`.env`/`.pem`/`.key`.

## Security invariants (do not weaken)

- Install goes through `import_plugin_from_bytes` only — it drops `..`/absolute
  zip entries and re-checks containment with `resolve().is_relative_to(dest)`.
  Never hand-roll extraction into `uefn_plugins/`.
- Size caps enforced at install: 32 MB compressed (`MAX_PLUGIN_ZIP_BYTES`),
  128 MB declared uncompressed (`MAX_PLUGIN_UNCOMPRESSED_BYTES`, zip-bomb guard).
  Extraction happens **before** the trust prompt — the caps are the only gate.
- Store downloads are sha256-verified client-side against the catalog response.
- Store catalog is **anonymous** (published items only). Free downloads stay
  anonymous. **Paid** items require DuckyOS sign-in + a recorded purchase
  (server-enforced); never weaken that gate. Publishing needs a staff API key;
  `my-items` needs sign-in.
- Store install refuses to overwrite a `source: local` plugin with the same id.
- Local plugins run with full app permissions — the one-time trust confirm on
  first enable is the user's only warning. Don't auto-trust in code.

## Develop / ship (Store only)

**Always install and update from Settings → Store.** Never use Install from
file, never `cp` into `%LOCALAPPDATA%/UEFN-Ducky/uefn_plugins/`, never tell
the user to sideload a zip. Publish → Store Update is the loop.

**AI one-liners** (see `docs/publish-to-uefn-ducky-store.md`):

```bash
./release/publish_app.sh                          # desktop Setup → Store
./release/publish_plugin.sh galaxycraft           # one plugin → Store
./release/publish_plugin.sh --list
```

1. Create a new GitHub repo `UEFN-Ducky/uefn-plugin-<id>` and clone it into
   `Documents/GitHub/uefn-plugins/uefn-plugin-<id>/`.
2. Copy Discord `scripts/build_zip.py` / `release.py` (same pattern).
3. Bump `version` in `plugin.json`.
4. Commit + push that repo, then
   `py -3 scripts/release.py --publish` from the clone root.
5. In app: **Settings → Store → Install / Update** → Enable.

**Never** `sync_seed.py`, never pack plugins into the EXE. Publish → Store →
Install/Update is the only loop.

## Publish to UEFN Ducky Store (your site catalog)

Requires site plugin `uefn-ducky-store` installed/enabled and an API key with
`mcp_remote.plugins` (or `mcp_remote.plugin.uefn-ducky-store`).

```bash
# bump version in plugin.json, then:
./release/publish_plugin.sh <id> --changelog "vN: …"
```

Env (auto-loaded from `~/.cursor/mcp.json` `uefn-duckyos-site` Bearer if unset):

- `DUCKYOS_API_KEY` — staff key for `https://uefnducky.org/api/v1/mcp`
- `DUCKYOS_BASE_URL` — optional override (must be UEFN site, not apex)
- `UDS_CATEGORY` — default `plugins` (skills use `skills`)

MCP equivalents: `uds_ensure_category` → `uds_release` (`zipB64`, `publish: true`)
on **uefn-duckyos-site**.

In-app users: Store → Install / Update. Download path detects `plugin.json` →
plugin vs skill pack.

## Manage installs

| Action | Where | Result |
|--------|-------|--------|
| Install / Update | Store card | AppData copy, `source=store` |
| Enable / Disable | Store card | Contributions + tools gate |
| Uninstall | Store card | Deletes AppData folder |

`source`: `store`, `local`, or `ai`. Agents never use Install-from-file.

## AI-made plugins (chat duckies)

**In-app duckies:** `skill_read_subskill("ducky", "ai_plugins")` (shipped pack).
**IDE agents:** also [references/ai_made_plugins.md](references/ai_made_plugins.md).

Shared per-install drafts (any duckie / IDE agent can edit any draft — not per-AI):

`%LOCALAPPDATA%/UEFN-Ducky/ai_plugins/<id>/`

Flow:

1. `ducky_plugin_reference` — contribution + `register(api)` cheat sheet
2. `ducky_plugin_scaffold(id, label, description)` — create draft
3. `ducky_plugin_write_file` / `read_file` / `list` — path-jailed to the draft only (cannot touch core files)
4. `ducky_plugin_validate` — `plugin.json`, `py_compile`, bundled skills
5. `ducky_plugin_install` — zip → `import_plugin_from_bytes(source="ai")` into `uefn_plugins/`
6. User trusts once (Settings → Store confirm; agents cannot pass `trust_local` for `source=ai`)
7. Iterate: edit draft → validate → install again (same-source replace → live reload)

Enable / disable / uninstall the installed copy with `ducky_store_set_enabled` /
`ducky_store_remove`. Cross-source overwrite is refused (ai ≠ store ≠ local).

Tools are intent-unlocked for phrases like "create a plugin", "theme", "AI plugin",
`customize the app`. Implementation: `ducky_app/backend/tools/panel_ai_plugins.py`.

### Skill authoring from chat

- `ducky_skills_create_pack` — new user-owned pack
- `ducky_skills_write_subskill` — **additive** on Store packs (`origin: user`);
  Store updates preserve those refs. Overwriting store-origin SKILL.md / refs is refused.

## Checklist for a new plugin

- [ ] `uefn-plugins/uefn-plugin-<id>/plugin.json` valid id + version
- [ ] `backend/register()` imports tools; tools check `is_plugin_enabled`
- [ ] React gates any new Settings/dock/editor UI on contributions
- [ ] No secrets in tree or zip
- [ ] Self-check passes: `cd ducky_app && py -m backend.uefn_plugins.test_uefn_plugins`
- [ ] `./release/publish_plugin.sh <id>` + in-app **Store → Install / Update** (never from file, never sync_seed)

## Key code paths

| Area | Path |
|------|------|
| Host / contributions | `ducky_app/backend/uefn_plugins/host.py` |
| Plugin webview (Phase 2) | `ducky_app/backend/uefn_plugins/webview.py` + `web/src/plugin-ui/` |
| Install / uninstall (Store) | `ducky_app/backend/uefn_plugins/store.py` |
| AI plugin drafts / MCP tools | `ducky_app/backend/tools/panel_ai_plugins.py` |
| Panel API | `ducky_app/frontend/ui_web/panel_api.py` |
| Store client | `ducky_app/frontend/duckyos_account.py` |
| UI hooks | `ducky_app/frontend/ui_web/web/src/hooks/usePluginContributions.ts` |
| Store tab | `…/views/settings/StoreTab.tsx` |
| Self-check | `ducky_app/backend/uefn_plugins/test_uefn_plugins.py` |
