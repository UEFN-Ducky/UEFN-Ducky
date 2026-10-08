---
description: "Testing plugins: validate, test and errors after every change, what each check does, and how to write tools and nodes that pass"
metadata:
  order: 4
  label: "Plugins: testing and errors"
  default_enabled: false
  load_condition: "After any plugin change, or when a plugin fails to load, a panel is blank, a tool or workflow node errors"
---

# Testing plugins

A plugin is done only when all three are clean. Run them **after every change**:

1. `ducky_plugin_validate(id)`: manifest, Python syntax and the plugin rules. Every
   problem names the file and line and says how to fix it.
2. `ducky_plugin_test(id)`: installs the draft and exercises it end to end.
3. `ducky_plugin_errors(id, since)`: everything that went wrong at run time.

Fix the draft and run them again until validate has no errors, the test report says
`"ok": true`, and errors since the test started (`errors_since` in the report) is
empty. Don't tell the user it works before that.

## What validate checks

- `plugin.json` is valid; backend files compile; no secrets in files.
- `skills/<id>/SKILL.md` exists.
- Every panel RPC has an MCP tool: RPC `save` needs a tool named `save` or
  `<something>_save` (`card_shop_save`).
- At least one workflow node is declared, every declared node has a
  `register_pipeline_node` handler, and a template uses one of them.
- The backend never reads `.py` source or loads `.py` files by path.
- UI files (`.html`, `.css`, `.js` outside `backend/` and `skills/`): no color
  literals, no `var(--x)` the app doesn't set (unless the plugin defines `--x`), and
  a visible focus style when there are panels (the UI kit counts). Theme files under
  `appearance.*` and vendored `vendor/` / `*.min.*` files are exempt.

## What test does

- Checks the UI files, then validates and installs the draft the usual way.
- Turns it on. The **first** test of an AI plugin the user hasn't confirmed returns
  `needs_trust`: stop, the user confirms once in Settings → Store, then test again.
- Calls **each MCP tool** with only its required arguments, each a safe sample: the
  schema default, else `""`, `0`, `false`, `[]` or `{}`. A raised exception fails the
  check; `{"ok": false, …}` passes with a note. Tools listed in
  `agent.tools.destructive_tools` are skipped.
- Runs **each workflow node** with an empty config (declared defaults only) and no
  inputs. It must return a dict; an exception fails.
- Opens **each panel**, waits a few seconds and fails it on any panel error. If the app
  window isn't up the panel check is skipped: open Ducky and test again.
- Fails on backend load errors.
- Everything runs on a **throwaway copy** of the plugin's data (docs, files, sensitive
  docs, cache and prefs); the user's data is never touched. A call that takes over
  30 seconds fails as timed out.

So write tools and nodes that:

- **Never raise on empty or bad input.** Return
  `{"ok": False, "error": "<what to give>"}` instead.
- Give optional arguments defaults (`kind: str = ""`), but **no default that acts
  outside the plugin's data**: the throwaway copy covers only the plugin's own data, so
  a tool that switches the theme, fires a hook, writes project files or calls UEFN must
  do nothing until it is given a real target (`theme: str = ""` → "Pick a theme").
- List tools that delete, overwrite many things, spend money or post outside the PC in
  `agent.tools.destructive_tools` (the agent then asks the user first, and tests skip
  them).
- Return quickly; long jobs start work and return an id, or wait with a timeout.

## Reading errors

`ducky_plugin_errors(id, since=0, limit=50)` → `{errors: [{when, source, message,
panel?, tool?, node?, stack?}], counts}`, newest first. `since` is epoch seconds.

| `source` | Means | Usual fix |
|---|---|---|
| `load` | `register(api)` or an import failed, or a team plugin is paused | The message and stack name the line. Relative imports; no missing modules. |
| `panel_crash` | The panel page crashed | Check the page's script for a top-level error. |
| `panel` | Uncaught error, rejected promise or `console.error` in a panel (at most 20 per page load) | Catch bridge errors and show them in a `dk-error` state. |
| `tool` | An MCP tool raised | Return `{"ok": False, "error": …}` for expected problems. |
| `node` | A workflow node raised or returned an error | Read inputs and config defensively. |

## Other checks

- Verse templates: `verse_template_verify(template_ids)` with UEFN open builds them
  in the project and maps compile errors back to each template.
- Workflows: `run_workflow` the plugin's template, or `test_workflow_node` for one node.
- A tool from a chat: call it like the user would (through the chat's tools) and read
  the result, including with the UEFN editor closed (`listener=False` tools must
  still work).
