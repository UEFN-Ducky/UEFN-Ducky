---
description: "Known plugin problems and their fixes: scaffold bugs, listener gate, sandbox limits, theme keys, compiled backends, walkthrough targets, hooks"
metadata:
  order: 4
  label: "Plugins: known problems"
  default_enabled: false
  load_condition: "Before calling a plugin done, or when a plugin tool, panel, node, theme, tour or sound doesn't behave"
---

# Plugin known problems

Check these before you tell the user a plugin works.

## Backend

| Problem | Do this |
|---|---|
| The scaffold registers panel RPCs as `lambda params=None: …`, but the host calls RPCs with **keyword arguments** (`fn(**params)`), so any call with params fails. | Name the parameters: `api.register_panel_rpc("list", lambda kind="": list_items(kind))`. |
| Tools without `listener=False` are refused with "UEFN listener offline" whenever the UEFN editor is closed (the scaffold's tools too). | `@api.tool(listener=False)` on every tool that doesn't call `api.listener` or a UEFN tool. |
| A tool or node that raises on empty input fails `ducky_plugin_test`. | Return `{"ok": False, "error": "…"}` for missing or bad input. |
| Tool names are global: a name another plugin already uses is refused. | Prefix every tool with the plugin id (`card_shop_list`); hyphens become underscores. |
| `from backend import x` / `import backend.x` loads the **app's** package. | Import your own modules relatively: `from . import x`. |
| Reading your own `.py` (`__file__`, `inspect.getsource`) or loading `.py` by path breaks once the plugin is published (compiled). Validate rejects it. | Keep data in `.json` / `.txt` beside the module or in `assets/`; import modules. |
| `scripts/`, `deploy/`, `tests/`, `test_*.py`, `*.zip`, `*.bin` never ship. | Keep runtime files elsewhere. |
| `api.data.put` over 1 MB raises. | One doc per record; big blobs go in `put_file`. |
| Writes raise `PermissionError` when the team copy is read-only or access was lost. | Catch it in tools and return the message as `{"ok": False, "error": …}`. |
| `api.data.delete(key)` doesn't remove a `sensitive=True` doc while the plugin uses a team. | `api.data.personal().delete(key)`. |
| `api.call_tool` raises `ValueError` when the tool fails; a destructive tool it calls still waits for the user's Allow. | Catch and report; don't call destructive tools from nodes that run unattended. |
| Push events reach every panel subscribed to that type. | Prefix event types with your plugin id (`card_shop_changed`). |
| Workflow nodes pass on only the returned keys that match an output pin id. | Return `{"ok": True, "<pin id>": value, …}` for every declared output. |
| A secret read with `get_key` is empty until the user saves it. | Return a clear error naming the Settings tab; register `api.register_secret_test`. |

## Panels

| Problem | Do this |
|---|---|
| Theme snippets that check `key.startsWith("--")` never apply anything: `theme.get` / `appearance_theme` keys come **without** `--`. | Link the UI kit, or `setProperty("--" + key, value)`. |
| `var(--font-family)`, `var(--primary)`, `var(--text-color)` don't exist: validate rejects them. | `var(--font-ui)`, `var(--accent)`, `var(--fg)`; the full list is in `plugin_look`. |
| The kit tag with an absolute path or the wrong depth leaves the panel unstyled. | `../../_kit/ducky.js` from `ui/<page>.html`, one more `../` per folder. |
| The iframe is sandboxed without same-origin or modals: `alert`, `confirm`, `prompt`, `localStorage`, cookies and `window.top` navigation fail; POSTs to `/__panel_*` are rejected. | Show messages in the page (`dk-error`, a confirm row); keep state with `prefs.*` / `data.*`; talk to the host with the bridge. |
| Hidden tabs keep running (timers, polling, video) and `document.visibilityState` stays `visible`. | Pause on `panel.visibility` with `visible: false`. |
| Moving a tab to a floating window reloads the page. | Restore from `prefs` / `data` on load. |
| Scope switches and syncs change the data under an open panel. | Re-read on `plugin_scope_changed`. |
| Loading your own scripts with `fetch` + `eval`. | `<script src>` / `<link href>` for your files; outside scripts only from unpkg, jsDelivr, cdnjs or esm.sh. |
| `prefs.set` keeps only booleans, strings, numbers and `null`. | Store objects with `data.put`. |

## App surfaces

| Problem | Do this |
|---|---|
| Walkthrough steps only resolve **registered** target ids; a plugin's header button has none, so a step pointing at it never shows. | Use ids from `ducky_ui_list_targets` (`settings.tab.<Tab>` for your Settings tab, `header.automations`, `header.settings`, `shell.header`) and name the button in the step text. |
| A plugin can't switch the active Appearance theme, skin or effect (`ducky_settings_set` refuses those keys). | List the themes in a tool and show the user where to pick (`ducky_ui_show`, `settings.tab.appearance`). |
| Plugin `hooks` only add names to Settings → Sounds; nothing fires them except a `shell.boot` script. | Map `sounds` to the built-in hooks (`tab.changed`, `settings.opened`, `llms.settings`, `model.picker`, `store.opened`, `agent.selected`, `agent.done`, `agent.error`, `verse.errors`), or fire your own from `shell.boot` (only if asked). |
| `listener/` handlers load only for enabled plugins and only in a running UEFN. | Enable the plugin with UEFN open (Ducky reloads the listener), then call `api.listener`. |
| A Text to Image backend tool must take `wait` and `output_dir` (and `confirm_spend` when paid) and return `downloaded: [paths]`. | Follow `plugin_examples_app` § Image generators. |
| The first `ducky_plugin_test` of a new AI plugin stops with `needs_trust`. | Stop and let the user confirm once; then test again. |
| `ducky_plugin_test` skips the panel checks when the app window isn't open. | Ask the user to open Ducky, test again. |
| Store installs don't update when `version` stays the same. | Raise `version` before every publish. |
