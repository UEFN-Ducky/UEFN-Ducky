---
description: "Known plugin problems and their fixes: the listener gate, sandbox limits, theme keys, compiled backends, global names, test side effects, tour targets"
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
| A tool left at the default `listener=True` is refused with "UEFN listener offline" whenever the UEFN editor is closed. | `@api.tool(listener=False)` on every tool that doesn't call `api.listener` or a UEFN tool. |
| A panel RPC written as `lambda params=None: …` fails: the host calls RPCs with the panel's params as **keyword arguments** (`fn(**params)`). | Name the parameters: `api.register_panel_rpc("list", lambda kind="": list_items(kind))`. |
| A tool or node that raises on empty input fails `ducky_plugin_test`. | Return `{"ok": False, "error": "…"}` for missing or bad input. |
| `ducky_plugin_test` calls every tool and node with empty input, and its throwaway copy covers only the plugin's own data: a default that switches the theme, fires a hook, writes project files or calls UEFN really does it. | No acting defaults (`theme: str = ""`), or list the tool in `agent.tools.destructive_tools`. |
| Tool names, listener command names, hook ids and Verse template ids are global: a name another plugin already uses is refused or collides. | Prefix every one with the plugin id (`card_shop_list`, `card_shop.sold`); hyphens become underscores in Python names. |
| `from backend import x` / `import backend.x` loads the **app's** package. | Import your own modules relatively: `from . import x`. |
| Reading your own `.py` (`__file__`, `inspect.getsource`) or loading `.py` by path breaks once the plugin is published (compiled). Validate rejects it. | Keep data in `.json` / `.txt` beside the module or in `assets/`; import modules. |
| `importlib.reload()` of your own modules raises `SystemError` in the compiled build, so whatever calls it (often a save) breaks. Validate rejects it. | Reload only when running from source (skip it when `'__compiled__' in globals()`), or don't reload. |
| Logs, tool errors and node results end up in Settings → Errors and in Support feedback. `api.log` scrubs folders, emails and keys, but not what you put in a result. | Never put the user's chat text, keys or files in logs or errors; secrets go in `secret_keys`. |
| `scripts/`, `deploy/`, `tests/`, `test_*.py`, `*.zip`, `*.bin` never ship. | Keep runtime files elsewhere. |
| `api.data.put` over 1 MB raises. | One doc per record; big blobs go in `put_file`. |
| Writes raise `PermissionError` when the team copy is read-only or access was lost. | Catch it in tools and return the message as `{"ok": False, "error": …}`. |
| `api.data.delete(key)` doesn't remove a `sensitive=True` doc while the plugin uses a team. | `api.data.personal().delete(key)`. |
| `api.call_tool` raises `ValueError` when the tool fails; a destructive tool it calls still waits for the user's Allow. | Catch and report; don't call destructive tools from nodes that run unattended. |
| Push events reach every panel subscribed to that type. | Prefix event types with your plugin id (`card_shop_changed`). |
| Workflow nodes pass on only the returned keys that match an output pin id. | Return `{"ok": True, "<pin id>": value, …}` for every declared output. |
| A secret read with `get_key` is empty until the user saves it. | Return a clear error naming the Settings tab; register `api.register_secret_test`. |
| Settings `select` options are `{value, label}`, workflow `config_fields` options `{id, label}`; a settings `default` is only what Settings shows until the user changes it. | Use the right shape for each; give every settings read the same fallback. |

## Panels

| Problem | Do this |
|---|---|
| Theme keys from `theme.get` / `appearance_theme` come **without** `--`; code that checks `key.startsWith("--")` never applies anything. | Link the UI kit, or `setProperty("--" + key, value)`. |
| `var(--font-family)`, `var(--primary)`, `var(--text-color)` don't exist: validate rejects them. | `var(--font-ui)`, `var(--accent)`, `var(--fg)`; the full list is in `plugin_look`. |
| The kit tag with an absolute path or the wrong depth leaves the panel unstyled. | `../../_kit/ducky.js` from `ui/<page>.html`, one more `../` per folder. |
| The iframe is sandboxed without same-origin or modals: `alert`, `confirm`, `prompt`, `localStorage`, cookies and `window.top` navigation fail; POSTs to `/__panel_*` are rejected. | Show messages in the page (`dk-error`, a confirm row); keep state with `prefs.*` / `data.*`; talk to the host with the bridge. |
| Hidden tabs keep running (timers, polling, video) and `document.visibilityState` stays `visible`. | Pause on `panel.visibility` with `visible: false`. |
| Moving a tab to a floating window reloads the page. | Restore from `prefs` / `data` on load. |
| Scope switches and syncs change the data under an open panel. | Re-read on `plugin_scope_changed`. |
| Loading your own scripts with `fetch` + `eval`. | `<script src>` / `<link href>` for your files. |
| Anything from the internet (CDN scripts like unpkg or Tailwind's, outside stylesheets or fonts, `import` from a URL) is blocked, so the panel loads broken or unstyled. Validate rejects it. | `ducky_plugin_vendor` the exact npm version into `ui/vendor/` and load it from there; for styling use the UI kit and Appearance variables. |
| `prefs.set` keeps only booleans, strings, numbers and `null`. | Store objects with `data.put`. |

## App surfaces

| Problem | Do this |
|---|---|
| Tour and Show me steps only resolve **registered** target ids. | Use ids from `ducky_ui_list_targets`; a plugin header button is `header.button.<plugin id>.<button id>` (`header.button.<button id>` in its own walkthrough), its Settings tab `settings.tab.<tab id>`. |
| On a narrow window the header buttons fold into a menu, so a step on a plugin header button can't show until that menu is open. | Start the tour on `shell.header` (the top bar) and name the button. |
| `api.set_appearance_profile` switches only to the plugin's **own** `appearance.profiles`, and replaces whatever the user had picked. | Tell the user you switched it and that Settings → Appearance switches back. |
| `api.emit_hook` fires only hooks the plugin declares, and plays nothing until the user puts a sound on that hook. | Declare the hook in `contributes.hooks`; show the user Settings → Appearance → Sounds. |
| `listener/` handlers load only for enabled plugins and only in a running UEFN. | Enable the plugin with UEFN open (Ducky reloads the listener), then call `api.listener`. |
| A Text to Image backend tool must take `wait` and `output_dir` (and `confirm_spend` when paid) and return `downloaded: [paths]`. | Follow `plugin_examples_app` § Image generators. |
| The first `ducky_plugin_test` of a new AI plugin stops with `needs_trust`. | Stop and let the user confirm once; then test again. |
| `ducky_plugin_test` skips the panel checks when the app window isn't open. | Ask the user to open Ducky, test again. |
| Store installs don't update when `version` stays the same. | Raise `version` before every publish. |
| A compiled build needs UEFN Ducky 1.2.357 or newer; the Store hides it from older apps. | The build sets `min_app_version` to at least 1.2.357; never lower it by hand. |
| A compiled build made outside the build engine (Nuitka or zig run by hand) uses the build PC's CPU features, so on other PCs Ducky dies with illegal instruction `0xC000001D` while loading it. | Build only with the build engine (it compiles and links for baseline x86-64). Before publishing: `objdump -d <file>.pyd \| grep -cE '%(y\|z)mm'` must print `0`. |
