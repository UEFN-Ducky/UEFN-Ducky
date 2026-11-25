---
description: "Sharing plugins: the Store is the only way, signed licenses, what compiles, and how to write code that survives compiling"
metadata:
  order: 4
  label: "Plugins: publishing and licenses"
  default_enabled: false
  load_condition: "User wants to share, publish, update, protect or sell a plugin, or asks why a plugin won't load after publishing"
---

# Publishing plugins

## Who can run what

- A **draft** (`ducky_plugin_*`) runs only on its author's PC.
- Other people get a plugin **only through the UEFN Ducky Store**. Never zip uploads,
  Install-from-file, shared folders, git, or copying files to another PC.
- The publishing tools (`ducky_plugin_build`, `ducky_plugin_publish`,
  `ducky_plugin_status`) come with the **Ducky Account** plugin, which also signs this
  PC in to the Store. Its skill has the steps and targets:
  `skill_read_subskill("account", "publishing")`. Without it, say that publishing needs
  Ducky Account installed and signed in.
- Every Store download carries a **license**, free plugins included. It is baked in
  when the plugin is built. A local draft never needs one. Store versions are signed:
  a changed zip is refused, and a compiled copy can't be sideloaded. A plugin whose
  license no longer covers this account pauses and resumes when it does again.

Paid licenses are not available yet: don't promise a price or a sale.

**Platforms:** a compiled build runs only on the OS and CPU it was built on, and
`ducky_plugin_publish` builds for this PC's. Windows + Linux x86_64 + Linux arm64 in one
release, and code that runs on both: `skill_read_subskill("ducky", "plugin_platforms")`.

## Before you publish

1. The draft passes `ducky_plugin_validate`, `ducky_plugin_test` and
   `ducky_plugin_errors` clean (`plugin_testing`). Plugins with Verse templates also
   pass `verse_template_verify` with UEFN open.
2. `ducky_plugin_validate` shows none of the Store's refusals in its warnings. Every
   Store build (public or a team's private one) refuses, before anything uploads:
   - no icon (`ai_plugins` rule 14: ask, then `ducky_plugin_set_icon`);
   - no `"data_sharing"`, or a team folder inside the island (rule 8);
   - a personal path in a shipped file;
   - a build over 32 MB.
   Fix the team warnings too: records in `api.data`, own files in a `team_folders`
   folder outside the island.
3. Raise `version` in `plugin.json` (semver) so installs see an update. The Store keeps
   every version it was sent, a rejected one too: never send the same number twice.
   Ducky Account's publish refuses a version the Store already has, and an update with
   no release notes, before it builds. The build
   report lists skill files without a description and tools with a weak first docstring
   line: fix them first, or agents won't find the plugin (`ai_plugins` → Make it findable).
4. Ask the user where it goes and for one line of release notes, then follow the
   Account skill.

## What compiles (write code that survives it)

A published backend is compiled into one module, `uefn_plugin_<id>` (hyphens become
underscores). No `.py` source ships. UI `.js` / `.css` are minified (vendored
`*.min.*` files are left as they are).

- **Never read your own source**: no `inspect.getsource`, no `open(__file__)`, no
  reading any `.py` file. Keep what you need as data.
- **Never load `.py` files by path**: no `importlib.util.spec_from_file_location`,
  `runpy.run_path`, `SourceFileLoader`, `exec(open(…).read())`. Import modules:
  `from . import store`.
- **One long module cut into files** is the one exception. A module may run files
  beside it in its own namespace when their names are a marked list:
  `_PARTS = ("_kit.py", "_roads.py")  # ducky-build: one module`, then your own loop
  that `exec`s each file into `globals()` (that is how it runs from source). The build
  compiles the listed files into the module and leaves the list empty, so the loop
  runs nothing there. Every part starts with the module's own `from __future__` lines.
- **A build that does not load is not written.** The build unpacks the finished zip
  the way an install lays it out and imports the backend in a process of its own.
  `Build failed: The build does not load…` carries Python's own error: fix what it names.
- **Import your own modules relatively.** `import backend.x` or `from backend import x`
  imports the *app's* `backend` package, not yours.
- **Data files** go in `assets/` or as `.json` / `.txt` next to the module.
  `Path(__file__).parent / "cards.json"` still works after compiling (non-Python
  files in `backend/` are kept beside the compiled module).
- **It must run on every CPU.** Compile only through UEFN Ducky's build engine (the
  publishing tools or a plugin's `scripts/release.py`), never by running Nuitka or zig
  yourself. zig builds for the CPU of the PC it runs on; the engine compiles *and
  links* for baseline x86-64 instead. A build linked with the build PC's AVX-512 stops
  Ducky opening (illegal instruction `0xC000001D`) on every CPU without it. Before
  publishing a compiled build, check the `.pyd`:
  `objdump -d <file>.pyd | grep -cE '%(y|z)mm'` must print `0`.
- **Not shipped:** `scripts/`, `deploy/`, `tests/`, `test_*.py`, `node_modules/`,
  dot-folders, `*.zip`, `*.bin`. Don't put anything the plugin needs there.
- **Other runtimes:** `listener/` is left as source because UEFN's own Python
  imports it (plain Python that `import unreal` can run; no app imports). Blender
  code runs inside Blender: send it as text to `blender_execute_blender_code`, kept
  as a string constant or a `.txt` asset, never a `.py` you read.

`ducky_plugin_validate` flags source reads and `.py` loading before you publish.
