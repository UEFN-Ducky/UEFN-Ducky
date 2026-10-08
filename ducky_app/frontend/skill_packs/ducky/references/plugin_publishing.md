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
- Every Store download carries a **license**, free plugins included. It is baked in
  when the plugin is built, from the build/publish arguments (visibility and team). A
  local draft never needs one. Store versions are signed: a changed zip is refused,
  and a compiled copy can't be sideloaded.

| Target | Who can install | Notes |
|---|---|---|
| `team` | Members of that one team | Live for the team at once. If a member loses access to the team, the plugin pauses on their PC ("Paused, no access to …") and resumes when access returns. |
| `public` | Anyone | Goes to review first: Store staff rebuild it from its source and publish that build, signed. |

Paid licenses are not available yet: don't promise a price or a sale.

## Publish

1. The draft passes `ducky_plugin_validate`, `ducky_plugin_test` and
   `ducky_plugin_errors` clean (`plugin_testing`). Plugins with Verse templates also
   pass `verse_template_verify` with UEFN open.
2. Raise `version` in `plugin.json` (semver) so installs see an update.
3. Ask the user where it goes (`ducky_ask_user`: their team or public), which team owns
   it (their team ids are in `ducky_team_plugins()` / the Account panel), and for one
   line of release notes.
4. Optional check: `ducky_plugin_build(id, visibility="team"|"public", team_id="")`
   compiles it into a Store-ready zip and reports files, sizes, warnings and sha256.
   It doesn't publish. Fix the warnings. The first build on a PC sets up a build kit
   once and takes a few minutes.
5. `ducky_plugin_publish(id, target="team"|"public", team_id="", notes="")`: asks the
   user first, then builds and uploads it (the backend compiled; the private source
   only for the team's managers and Store reviewers). `team_id` is the team that owns
   the plugin: needed for a **new** plugin either way; a new version keeps its owner.
6. `ducky_plugin_status(id)` confirms it: where it's published (`published_to`),
   `versions`, and `protected` (the latest version is compiled). `protected: false`
   means an older version went up as readable source: publish again.

The Account panel offers the same (Plugins → Publish to team / Publish public, Edit
with Ducky). An app too old for publishing says "Update UEFN Ducky to publish
plugins."

## Edit a team's plugin

1. `ducky_team_plugins(team_id="")` lists that team's published plugins (no team: your
   own items).
2. `ducky_plugin_open_source(id, team_id="")` pulls the team plugin's private source
   into a local draft. It needs Manage plugins on that team, and asks first when a
   draft with that id already exists (it would replace it).
3. Edit the draft, validate, test, then publish it back with
   `ducky_plugin_publish(id, target="team", team_id=…, notes=…)`.

## Store review (Store staff only)

Only when the user is Store staff and asks: `ducky_store_review_queue()` lists public
submissions waiting; `ducky_store_review(slug, version="", approve=False, note="")`
approves (rebuilds from the submitted source on this PC, never running it, and
publishes that build signed) or rejects (a `note` for the author is required). Both
ask first.

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
