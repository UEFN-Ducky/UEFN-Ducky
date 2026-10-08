---
description: "Sharing plugins: the Store is the only way, build and publish to a team or public, licenses, team pauses, editing team plugins, and what compiles"
metadata:
  order: 4
  label: "Plugins: publishing and licenses"
  default_enabled: false
  load_condition: "User wants to share, publish, update, protect or sell a plugin, edit a team's plugin, or asks why a plugin won't load after publishing"
---

# Publishing plugins

## Who can run what

- A **draft** (`ducky_plugin_*`) runs only on its author's PC.
- Other people get a plugin **only through the UEFN Ducky Store**:
  `ducky_plugin_build` → `ducky_plugin_publish`. Never zip uploads, Install-from-file,
  shared folders, git, or copying files to another PC.
- Every Store download carries a **license**, free plugins included. Store versions
  are signed: a changed zip is refused, and a compiled copy can't be sideloaded.

| Target | Who can install | Notes |
|---|---|---|
| `team` | Members of that one team | If a member loses access to the team, the plugin pauses on their PC ("Paused, no access to …") and resumes when access returns. |
| `public` | Anyone | The owner reviews and rebuilds a public plugin before people get it. |

Paid licenses are not available yet: don't promise a price or a sale.

## Publish

1. The draft passes `ducky_plugin_validate`, `ducky_plugin_test` and
   `ducky_plugin_errors` clean (`plugin_testing`). Plugins with Verse templates also
   pass `verse_template_verify` with UEFN open.
2. Raise `version` in `plugin.json` (semver) so installs see an update.
3. Ask the user where it goes (`ducky_ask_user`: their team or public) and for one
   line of release notes. Publishing is outward: the tool shows an Allow card.
4. `ducky_plugin_build(id)`: builds the Store package (compiled backend, minified UI).
   The first build on a PC sets up a build kit once and takes a few minutes.
5. `ducky_plugin_publish(id, target, team_id, notes)`: `target` is `"team"` (with that
   team's id) or `"public"`.
6. `ducky_plugin_status(id)` confirms it: `published_to`, `versions`, `protected`.
   `protected: false` means an older version went up as readable source: publish
   again and the Store copy is built compiled.

The Account panel offers the same (Plugins → Publish to team / Publish public, Edit
with Ducky). An app too old for publishing says "Update UEFN Ducky to publish
plugins."

## Edit a team's plugin

1. `ducky_team_plugins(team_id)` lists the team's plugins.
2. `ducky_plugin_open_source(id)` brings its source to this PC as a draft (also for a
   plugin installed here without a draft).
3. Edit the draft, validate, test, then publish it back with
   `ducky_plugin_publish(id, "team", team_id, notes)`.

## What compiles (write code that survives it)

A published backend is compiled into one module, `uefn_plugin_<id>` (hyphens become
underscores). No `.py` source ships. UI `.js` / `.css` are minified (vendored
`*.min.*` files are left as they are).

- **Never read your own source**: no `inspect.getsource`, no `open(__file__)`, no
  reading any `.py` file. Keep what you need as data.
- **Never load `.py` files by path**: no `importlib.util.spec_from_file_location`,
  `runpy.run_path`, `SourceFileLoader`, `exec(open(…).read())`. Import modules:
  `from . import store`.
- **Import your own modules relatively.** `import backend.x` or `from backend import x`
  imports the *app's* `backend` package, not yours.
- **Data files** go in `assets/` or as `.json` / `.txt` next to the module.
  `Path(__file__).parent / "cards.json"` still works after compiling (non-Python
  files in `backend/` are kept beside the compiled module).
- **Not shipped:** `scripts/`, `deploy/`, `tests/`, `test_*.py`, `node_modules/`,
  dot-folders, `*.zip`, `*.bin`. Don't put anything the plugin needs there.
- **Other runtimes:** `listener/` is left as source because UEFN's own Python
  imports it (plain Python that `import unreal` can run; no app imports). Blender
  code runs inside Blender: send it as text to `blender_execute_blender_code`, kept
  as a string constant or a `.txt` asset, never a `.py` you read.

`ducky_plugin_validate` flags source reads and `.py` loading before you publish.
