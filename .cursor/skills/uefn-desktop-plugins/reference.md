# UEFN desktop plugin reference

Companion to [SKILL.md](SKILL.md).

## plugin.json fields

| Field | Type | Notes |
|-------|------|--------|
| `id` | string | Required. `^[a-z][a-z0-9_-]{0,63}$` |
| `kind` | string | `"plugin"` |
| `version` | string (SemVer) | `"1.0.8"` — JSON string (npm/Cargo style). Legacy bare ints still accepted by host/Store. |
| `label` | string | Display name |
| `description` | string | Store card |
| `min_app_version` | string | Declared; **not enforced** by host today |
| `default_enabled` | bool | Default `true`; auto-enable on first seed/store install |
| `secret_keys` | string[] | Credential **names** only |
| `source` | string | Host writes `bundled` \| `store` \| `local` \| `ai` — do not ship |
| `contributes` | object | See below |
| `backend` | object | `{ "entry": "backend", "register": "register" }` |

Dotted keys (`settings.tabs`) or snake_case both accepted by the host.

### contributes.settings.tabs

```ts
{ id: string, label?: string, icon?: string, ui?: string }
```

`icon`: color asset path (`assets/icon.png` / `.svg`), raw emoji (`🎨`), or
named key (`chat`, `speaker`, … → emoji). Prefer assets; named keys are emoji
fallbacks (no line SVGs). Host inlines assets as a data URL for the left Settings
rail (colored image, not a mono glyph).

Host adds `plugin_id` at load time.

`ui` values:
- omitted / sections-only → host renders declarative `settings.sections` for that tab
- `builtin:…` → host React form (e.g. `builtin:account-settings`, `builtin:discord-settings`)
- `panel:<panelId>` → sandboxed HTML from `ui.panels` embedded in Settings

### contributes.shell.boot

```ts
{ entry: string }  // path inside plugin zip, e.g. "ui/boot.js"
```

When the plugin is **enabled**, the host loads `entry` as a classic script in the **main window** (not the iframe). Injected API:

```js
window.__duckyPluginHost.forPlugin(pluginId)
// { pluginId, prefs, llm.batchComplete, cache, mountChoiceDropdown }
```

`mountChoiceDropdown(el, { mode:"radio"|"checkbox", value/values, options, onChange })`
mounts the core ChoiceDropdown (panel button + radio/checkbox list — never native `<select>`).

Boot scripts can walk `document.body`. Register cleanup on disable:

```js
window.__duckyPluginBootCleanups[pluginId] = () => { /* stop */ };
```

Header action `settings:<tabId>` opens Settings to that tab id.

### contributes.appearance.profiles

Editable appearance profiles shown in Settings → Appearance while the plugin is enabled. Same token shape as built-in packs (Default / Light / Hacker). Selecting the profile applies the contribution as the baseline; users can customize colors in-place. Resets fall back to the contribution (not a blank Default). Customizations persist in `appearance_profile_patches` keyed by host profile id. The contribution itself is not written into `appearance_profiles`.

```ts
{
  id: string,           // local id; host UI id is `__plugin__:<pluginId>:<id>`
  name: string,
  foundation?: Record<string, string>,
  overrides?: Record<string, string>,
  status_overrides?: Record<string, Record<string, string>>
}
```

ThemeProvider remains authoritative for CSS variables from the active profile. Prefer registering a profile for colors; use `appearance.css` for structural chrome restyles.

### contributes.appearance.css

Stylesheets injected into the **main window** after ThemeProvider’s `:root` block (so they can override layout/fonts/radii). Unloaded when the plugin is disabled.

```ts
{ entry: string }  // e.g. "ui/theme.css"
```

### contributes.appearance.effects

Background effect scripts mounted into `#ducky-fx-root` (fixed, `pointer-events: none`, behind `.app-container`). Listed in Settings → Appearance under the Effects accordion (enable/disable + picker). Host effect id is `plugin:<pluginId>:<id>`. Built-in `matrix` is not a plugin contribution. Selecting a matching profile auto-picks that plugin’s effect (prefer `planets`); users can turn the effect off without clearing the choice.

```ts
{ id: string, label: string, entry: string }  // e.g. "ui/fx.js"
```

Effect scripts should draw into the mount root and register cleanup:

```js
const { root, key } = window.__duckyAppearanceFxMount; // set by host before script runs
window.__duckyAppearanceFxCleanups = window.__duckyAppearanceFxCleanups || {};
window.__duckyAppearanceFxCleanups[key] = () => { /* stop RAF / remove nodes */ };
```

### contributes.appearance.skin

Full chrome swap. Host suppresses default chrome paint (`body.appearance-skin-active`) and exposes empty portals. Listed in Settings → Appearance → Skin. Host skin id is `plugin:<pluginId>:<id>`.

```ts
{
  id: string,
  label: string,
  entry: string,   // e.g. "ui/skin.js"
  css?: string     // optional stylesheet, e.g. "ui/skin.css"
}
```

Before `entry` runs:

```js
const { slots, key, pluginId, skinId } = window.__duckyAppearanceSkinMount;
// slots.frame | slots.header | slots.left | slots.right  (HTMLElements)
window.__duckyAppearanceSkinCleanups = window.__duckyAppearanceSkinCleanups || {};
window.__duckyAppearanceSkinCleanups[key] = () => { /* remove injected nodes / stop anim */ };
```

Selecting a matching `appearance.profiles` entry from the same plugin auto-selects that skin (same local id preferred) and the plugin’s first / `planets` effect.

Conflict rule: do not fight ThemeProvider by setting `:root` CSS variables from boot/effect/skin scripts — ship an `appearance.profiles` entry instead.

### Bundled skills (no contributes key)

Ship Agent Skills packs inside the plugin zip:

```
skills/<skill-id>/
  SKILL.md
  references/*.md   # optional
```

- Folder name must equal the skill pack id (`^[a-z][a-z0-9_-]{0,63}$`) and
  match `name:` in SKILL.md frontmatter when present.
- Available while the plugin is installed (not gated on enable/disable).
- Host discovers them via `list_plugin_owned_skills()` / `skill.list_pack_ids()`.
- Collisions with standalone `skill_packs/<id>` or another plugin’s skill fail
  the whole plugin install.
- Delete by uninstalling the plugin — Skills Studio cannot delete plugin-owned packs.

### contributes.sounds

Audio files listed in Settings → Appearance → Sounds. Host sound ref is `plugin:<pluginId>:<id>`; served from `/plugin-ui/<pluginId>/<file>`.

```ts
{ id: string, label?: string, file: string }  // e.g. "assets/ping.mp3"
```

### contributes.hooks

Extra hookable events your plugin emits (shown as rows in Appearance → Sounds). Host hook id is `plugin:<pluginId>:<id>`.

```ts
{ id: string, label?: string }  // e.g. { id: "message", label: "Discord message" }
```

Emit from `shell.boot` (or any main-window script):

```js
window.dispatchEvent(new CustomEvent("ducky:hook", {
  detail: { id: "plugin:<pluginId>:<hookId>" },
}));
```

Built-in shell hooks (always available): `tab.changed`, `settings.opened`, `agent.selected`, `agent.done`, `agent.error`, `verse.errors`. Listen the same way if a plugin needs them.

### contributes.verse.templates

Rows in the sidebar **New file** Verse template picker while the plugin is enabled. Content is inlined at load. UI id is `plugin:<pluginId>:<id>`. Multi-file packs create a project folder with N `.verse` files.

```ts
{
  id: string,
  name: string,
  icon: string,            // emoji or short label shown in the row
  description?: string,
  file?: string,           // single-file path inside plugin zip
  content?: string,        // inline alternative to file
  folder?: string,         // project folder name for multi-file packs
  files?: Array<{ path: string, file?: string, content?: string }>,
  connects?: string[],     // e.g. ["registers:currency_provider", "needs:PlayerCore"]
  order?: number           // default 100; lower first among plugin rows
}
```

Reference: `plugins/uefn-plugin-verse/` (`contributes.verse.templates` + `templates/`).

### contributes.settings.sections

VS Code–style declarative settings:

```ts
{
  tab: string,              // settings.tabs id, e.g. "Discord"
  id: string,
  title?: string,
  description?: string,
  order?: number,
  properties: Array<{
    id: string,
    type?: "boolean" | "secret" | "string" | "select",
    default?: boolean | string,
    label?: string,
    description?: string,
    placeholder?: string,   // secret / string
    testable?: boolean,     // secret: show Test (needs api.register_secret_test)
    options?: Array<{ value: string, label?: string }>  // select
  }>
}
```

Rendered by `PluginSettingsSections` in the matching Settings tab. Prefs live in
localStorage (`uefn-plugin-ui-prefs[<plugin_id>][<property.id>]`). Secret
`testable: true` needs `api.register_secret_test(secret_key, fn)` in `register()`
(`fn(api_key) -> {ok, detail}`).

### contributes.dock.panels

```ts
{ id: string, title?: string, defaultSide?: string, ui?: string, css?: string }
```

`css` is typed but unused for dynamic load today.

### contributes.editor.kinds

```ts
{
  kind: string,           // host file kind, e.g. "unreal_asset" | "model"
  title?: string,
  ui?: string,            // "panel:<panelId>" → PluginFilePane for matching files
  suffixes?: string[]     // optional exact extensions (".uasset", ".fbx"); checked first
}
```

When a plugin is **enabled** and contributes an editor kind (with `ui: "panel:…"`),
`FileEditorPane` opens matching project files in a sandboxed `PluginFilePane`
iframe (`plugin.info` includes `filePath`). If no plugin claims the kind, the host
falls through to `BinaryFilePane` (View raw).

### Plugin `listener/` (optional Unreal Python)

Ship a `listener/` folder in the plugin zip. On enable/install the host overlays it
into `%LOCALAPPDATA%/UEFN-Ducky/listener/listener/plugins/<id_with_underscores>/`
and best-effort `reload_listener`. Modules use the same `@register("command")`
as core handlers. Call `listener.tick.register_heavy(name)` for slow commands.

### contributes.header.buttons

```ts
{
  id: string,
  title?: string,
  icon?: string,          // "assets/icon.png" | emoji | "chat"|"duck"|… (emoji fallback)
  action: string,         // "builtin:open-discord" | "panel:<panelId>"
  order?: number          // lower first; default 100
}
```

Phase 1: React renders contributed buttons in the app header (right of the
right-sidebar toggle). `builtin:…` actions are wired in
`pluginHeaderActions.tsx`. `panel:<panelId>` opens that plugin’s `ui.panels`
tab (Phase 2). Unknown actions are hidden (old app / new plugin degrade OK).

### contributes.ui.panels

Sandboxed plugin HTML (Phase 2). Isolated code:
`backend/uefn_plugins/webview.py` + `web/src/plugin-ui/` (see its README).

```ts
{
  id: string,             // panel id; header action "panel:<id>"
  title?: string,
  icon?: string,
  entry: string           // relative path under plugin dir, e.g. "ui/index.html"
}
```

- Served at `/plugin-ui/<pluginId>/<entry…>` (enabled plugins only; path-jailed).
- Editor tab id: `plugin:<pluginId>:<panelId>`.
- iframe sandbox: `allow-scripts allow-pointer-lock` (no `allow-same-origin`).
- Bridge methods: `plugin.info`, `prefs.get`, `prefs.set` (see plugin-ui README).
- Hostile `entry` with `..` / absolute paths is dropped at load.

### contributes.llm.providers

Gateway / LLM provider rows injected into **Settings → LLMs → Providers**
while the plugin is enabled. Opening a row slides in that provider’s details
(API key, coding agent, prompt-cache toggles, …). Host owns Test & Save /
secrets; the contribution only declares the row (same pattern as `tts.voices`).

```ts
{
  id: string,                 // provider id (e.g. "ollama")
  label: string,              // row label
  kind: "secret" | "url",     // password key vs local server URL
  secret_key?: string,        // credentials.dat key; default = id
  default_url?: string,       // for kind "url" (e.g. http://localhost:11434)
  order?: number              // sort among gateway rows (default 100)
}
```

Browse taxonomy: plugins that only contribute `llm.providers` are inferred as
`gateways` + `plugins` for Store filters. Publish with
`categories: ["plugins", "gateways"]`.

### contributes.llm.coding_agents

Coding-agent block inside that gateway’s **provider detail slide** while the
plugin is enabled. Host keeps the CLI adapter; the contribution only gates
listing / detect (same pattern as `llm.providers`).

```ts
{
  id: string,       // coding-agent id the host knows (e.g. "codex")
  label?: string,
  order?: number
}
```

Examples:
- Anthropic Store plugin: `llm.providers` (API key) + `llm.coding_agents: [{ id: "claude_code" }]`
- OpenAI Store plugin: `llm.providers` (API key) + `llm.coding_agents: [{ id: "codex" }]`
- Google Store plugin: `llm.providers` id `gemini` + `llm.coding_agents: [{ id: "gemini_cli" }]`
- Cursor Store plugin: `llm.providers` id `cursor` + `llm.coding_agents: [{ id: "cursor" }]`

### contributes.agent.tools

Optional metadata for tools registered via `api.tool()` (or legacy `@mcp.tool()`).
Object or `true`:

```ts
{
  category: string,
  tools?: string[],          // optional; api.tool() names are auto-tracked
  intent_pattern?: string    // regex; default \b<plugin_id>\b
}
```

If `true`: `{ category: <id>, tools: [] }`. Prefer `api.tool()` in `register()` —
names land in `_PLUGIN_TOOL_NAMES` / Settings → MCPs without listing them here.

### `api.tool` / `api.listener` (MCP hook)

```python
def register(api) -> None:
    @api.tool()
    def my_plugin_ping() -> str:
        return "pong"

    @api.tool(name="my_plugin_spawn", intent=r"\bmy_plugin\b")
    def spawn_helper(label: str) -> str:
        return str(api.listener("spawn_actor", {"label": label}))
```

- Collision with an existing FastMCP tool name → logged error, registration skipped.
- Disabled / not opted-in → `ValueError` with a clear enable hint.
- `api.listener` → `backend.bridge.send_command` (live editor).

## Host load order

1. `ensure_plugins_loaded()` → `seed_uefn_plugins()` (no-op; Store install only)
2. For each AppData `<id>/plugin.json` whose id is in `enabled_uefn_plugins`
3. Merge contributions; call `register(api)` once per process (`_REGISTERED`)

A plugin that throws during load is logged and skipped — it must never take
down core tool registration or app startup.

## UI gating helpers

`usePluginContributions.ts`:

- `pluginContributesSettingsTab(contrib, tabId)`
- `pluginContributesDockPanel(contrib, panelId)`
- `pluginContributesEditorKind(contrib, kind)`
- `pluginContributesHeaderButton(contrib, buttonId)`
- `pluginSettingsSectionsForTab(contrib, tabId)`

Live data: `get_uefn_plugin_contributions()`; push `uefn_plugins_changed`.

Discord wiring examples:

- Settings: `SettingsView.tsx` / `DiscordTab.tsx` — tab id `Discord`
- Declarative Placement: `settings.sections` → `PluginSettingsSections`
- Dock: `WorkspaceDockLayout.tsx` — panel id `groupchat`
- Header: `Header.tsx` + `header.buttons` → `builtin:open-discord`
- Webview (Phase 2): `ui.panels` → `web/src/plugin-ui/` + `backend/uefn_plugins/webview.py`

## Local vs Store rules

| Rule | Behavior |
|------|----------|
| Local occupies id | Store install blocked until uninstall |
| Local enable | Requires trust confirm → `trusted_local_uefn_plugins` |
| Zip size | 32 MB compressed (`MAX_PLUGIN_ZIP_BYTES`); install rejects > 128 MB declared uncompressed (`MAX_PLUGIN_UNCOMPRESSED_BYTES`) |
| Path traversal | Zip entries with `..` / absolute skipped + `resolve().is_relative_to(dest)` containment check |
| Uninstall | Deletes `uefn_plugins/<id>/` only |
| Store download | Anonymous, published items only, sha256-verified client-side |
| Seeding | **None** — EXE/repo never auto-copy plugins; Store install only |

## Scripts (copy from Discord)

| Script | Purpose |
|--------|---------|
| `scripts/build_zip.py` | Zip package → `deploy/<id>-<ver>.ducky-plugin.zip` |
| `scripts/release.py` | zip / `--publish` via `uds_release` (**never** `--sync-seed`) |

Skip from zip: `scripts/`, `deploy/`, `.git`, `README.md`, `__pycache__`, secret suffixes.

## Store MCP tools (site `uefn-ducky-store`)

`uds_list_items`, `uds_ensure_category`, `uds_create_item`, `uds_upload_version`,
`uds_publish`, `uds_unpublish`, `uds_release`.

Categories: `plugins` (desktop plugin zips), `skills` (skill packs).

API key needs `mcp_remote.plugins` (or per-plugin scope).

## Panel API surface

- `list_uefn_plugins`
- `get_uefn_plugin_contributions`
- `set_uefn_plugin_enabled(id, enabled, trustLocal?)`
- `uninstall_uefn_plugin(id)`
- `install_uefn_plugin_bytes(b64, source)`
- `open_uefn_plugins_folder`
- `duckyos_store_catalog` / store download+install

## App tool activation paths

Gated tools that still ship in the UEFN-Ducky EXE live under
`ducky_app/backend/tools/<domain>/`. Thin plugins import the new paths:

| Plugin | Example import |
|--------|----------------|
| uefn | `backend.tools.uefn.actors` |
| verse | `backend.tools.verse.verse` |
| leveldesign | `backend.tools.world.worldgen` |
| animation | `backend.tools.animation.sequencer` |
| modeling | `backend.tools.modeling.modeling` |
| scenegraph | `backend.tools.scene.scene_graph` |
| vfx | `backend.tools.vfx.niagara` |
| tester | `backend.tools.tester.suite` |
| translation | `backend.tools.integrations.translation_tools` |

Legacy flat imports (`backend.tools.actors`) still resolve via Release
`backend.util.import_compat` for older installed zips. Prefer the new paths in
source. Full layout: Release `ducky_app/backend/README.md`.

## Pitfalls

1. Declaring `settings.tabs` alone does not render UI — React must gate.
2. `builtin:` is not a remote UI host — app code required for new panels.
3. Secrets never in zip; use `set_key`.
4. `register()` once per process — gate tools with `is_plugin_enabled`.
5. `min_app_version` ignored by host today.
6. Extraction runs at install, before the trust prompt — never bypass
   `import_plugin_from_bytes` or its size caps when handling plugin bytes.
7. Mega `__init__.py` — split like Discord (`tools.py` + domain modules).

## Self-check

```bash
cd ducky_app && py -m backend.uefn_plugins.test_uefn_plugins
```

Covers: install + local-trust gate, contributions after reload, store-cannot-
overwrite-local, zip-slip (traversal entries dropped, nothing escapes AppData),
zip-bomb rejection, uninstall + reinstall. Run it after any change to
`store.py` / `host.py`.
