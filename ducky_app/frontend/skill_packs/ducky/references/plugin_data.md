---
description: "Plugin data: api.data docs and files, Local or one shared space, team locks, conflicts and history, sensitive data, encryption, read-only and lost access"
metadata:
  order: 4
  label: "Plugins: data"
  default_enabled: false
  load_condition: "A plugin stores records, files or settings, or the user asks where a plugin's data lives or about sharing it"
---

# Plugin data

Every plugin stores its data through the host: `api.data` in the backend, `data.*` /
`files.*` in panels. Never a folder, file, SQLite database or `%LOCALAPPDATA%` path
you pick: the host keeps each plugin's data per signed-in account and per scope,
encrypted on disk, and syncs shared copies.

## Docs and files

| Backend (`api.data`) | Panel bridge | What |
|---|---|---|
| `get(key, default=None)` | `data.get {key}` → `{key, value}` | One JSON doc (`None` / `null` when missing) |
| `put(key, value)` | `data.put {key, value}` → `{ok, changed}` | Write one doc (≤ 1 MB) |
| `items(prefix)` | `data.list {prefix, values: true}` → `{items}` | All docs under a prefix in one read |
| `keys(prefix)` | `data.list {prefix}` → `{keys}` | Just the keys |
| `delete(key)` | `data.delete {key}` → `{ok, deleted}` | Remove one doc |
| `put_file(path, bytes)` | `files.put {path, b64}` | A file (≤ 100 MB) |
| `get_file(path)` → bytes or `None` | `files.get {path}` → `{ok, b64}` | Read it back; there is no file path to hand out |
| `has_file(path)`, `files(prefix)` | `files.list {prefix}` | Exists / list `[{path, size, sha256}]` |
| `delete_file(path)` | `files.delete {path}` | Remove one file |
| `scope()` | `scope.get` → `{scope}` | `{kind, label, spaceId, readOnly}`: whose copy this is (`kind` is `personal`, `shared` or `sandbox`) |

- **One doc per entity**: `card.pip`, `pack.starter`, `run.2026-10-08`. Never one
  big `db.json`: two people editing different cards must not overwrite each other.
  Within one doc the last write wins.
- Doc keys `[a-z0-9._-]` up to 128 characters; file paths `[a-z0-9._/-]` up to 256,
  no `..`, no leading dot. Sanitize ids that come from users before using them in keys.
- `put` returns `changed: false` when nothing changed, so it is cheap to call.

## Local or one shared space

- The **user** picks, per plugin, whether its data is **Local** (this account on this
  PC) or one **shared space** offered by an installed plugin: Plugins → the plugin →
  Data. A plugin never mixes the two and needs no code for either.
  With no shared spaces on offer, Local is the only choice and the Data page says so.
- Every plugin says whether teammates may share its data: `"data_sharing"` at the top
  of `plugin.json`. Ask the user once before you scaffold ("Should your team share this
  plugin's data? A team copy gives everyone the same records with changes, locks,
  history and live editing, like source control.") and pass the answer to
  `ducky_plugin_scaffold(…, data_sharing=…)`. `ducky_plugin_validate` warns while it's
  missing, and a Store build (public or a team's private one) refuses the plugin.
  - `true`: the scope bar and Data picker offer shared spaces from the start.
  - `false` (browsing, personal tools, AI providers, nothing worth sharing): its data
    is always Local, it gets no scope bar and no Data picker, and switching it to a
    space is refused.
  Any other value is refused by `ducky_plugin_validate`.
- `ducky_plugin_data_scope(id, scope="")`: empty `scope` tells you which copy a plugin
  uses; `"local"` or a space id switches it. Switch only when the user asks: it asks
  them first, nothing is copied, open panels are told, the new copy syncs, and the
  plugin restarts on it with its open panels reloaded.
- Panels get `plugin_scope_changed` with a `reason`. `"synced"`: a sync brought
  teammates' changes into the same copy; `keys` lists the doc keys and file paths that
  changed (absent: re-read everything). Merge them into what is on screen and keep the
  open item, selection and scroll: never close what the person has open. `"switched"`:
  whose data it is changed (account, Local or a space): start over. Bursts arrive as one
  push, and a hidden panel gets one when shown. The backend reads the scope fresh on
  every call.
- Items that belong to one PC (diagnostics, per-device state, a "last opened" note) go
  under a prefix listed in `"data_local": ["qa.", "device/"]` at the top of
  `plugin.json`: they stay in the Local copy whatever copy the plugin shows, never sync,
  and never show teammates as someone editing them. Without it, every write of such an
  item goes out to the whole team and lands in their open panels.
- A backend that caches docs in memory registers `api.data.on_change(callback)` once in
  `register(api)`. After a sync changed this plugin's data, `callback(keys)` gets the doc
  keys and file paths that changed: drop those from the cache. It runs in every process
  that loaded the plugin (in the agents' bridge just before the plugin's next tool call),
  so never assume it runs on a particular thread, and keep it quick. An exception in it is
  logged and ignored.
- `prefs.*` and `cache.*` follow the scope too: a shared copy has its own prefs and
  cache on this PC (never synced), locked and unlocked with it.

## Shared copies

- Synced with everyone in the space; each space keeps its own copy and spaces never
  see each other's data. The plugin that offers the space syncs it; its skill says how.
- A shared copy can be **read-only** (paused by whoever runs the space). Writes then
  raise `PermissionError` with a message for the user. Catch it in tools and return
  `{"ok": False, "error": str(exc)}`.
- **Losing access** to the space locks its copy on this PC: reads come back empty and
  writes fail until access returns, then everything unlocks as it was. A copy that
  stays locked long enough leaves this PC (never the server's); changes made here that
  never went out wait until the person saves them to Local or lets them go (the bar's
  Details offers it).
- Whose data the user is looking at: the panel's scope bar shows it, and the host adds
  `scope` (`local` or the space's name) to every dict a tool of a data-using plugin
  returns. Say it in your reply when it matters.

## Team copies: changes, locks, conflicts, history

A team's shared copy keeps every change, like source control. The plugin needs no
code for it; these rules decide how well it works for people editing together.

- **One call is one change.** Everything one tool call or one panel RPC writes goes out
  together, named after the call and marked as the person's or their ducky's. Teammates
  get all of it or none of it. Write related docs in one call.
- **Locks.** Anyone can lock an item (a doc or a file) from the bar's Details, and a
  ducky can with `ducky_team_lock(plugin, unit, "acquire")` before a long edit (release
  it after). While someone else holds an item, a write to it raises `PermissionError`
  ("card.pip is locked by Ana since 3:02 PM. Your change wasn't saved."). Catch it in
  tools and return `{"ok": False, "error": str(exc)}`, as for read-only copies.
- **Every shared item locks to whoever changes it**, with no plugin code: the first change
  takes the lock for this PC (the write goes ahead), teammates see "Locked by Ana: <the
  item's name>" and can't change it until 30 seconds after Ana's last change went out, Ana
  closes the plugin's tab (once her changes went out), she releases it, or they ask. The lock is named by the doc's own `name`, `title` or `label`
  field (else the key), so give docs a `name`. Change it per prefix with
  `"data_locks": {"<key prefix>": "auto" | "required" | "none"}` in `plugin.json` (the
  start of a doc key or file path; the longest match wins; `""` is every item):
  - `auto` (the default): as above.
  - `required`: the write waits up to 3 s for the lock and is refused without it.
  - `none`: never locks, for data many people append to at once (logs, chat rows).
  Items under `data_local` never lock: they never leave this PC.
- **Conflicts.** Docs edited on two PCs at once merge field by field. Arrays of objects
  that all have an `id` merge by `id`; other arrays are replaced whole. When both sides
  changed the same field, the team's version stays and this PC's is kept as a conflict
  for the person (Keep mine, Keep theirs, Compare in the bar's Details). Files are never
  merged: the team's version stays and this PC's is kept beside it. So keep fields that
  people edit separately in separate keys or `id`'d objects, and big blobs in files.
- **History.** Every change is kept with who made it and when. The bar's Details shows
  it; restoring an item, or the whole plugin to an earlier change, is a new change that
  everyone gets, never a rewrite.
- **Live.** On a team copy, the bar's Live menu lists the live groups by host: Start
  live goes into the biggest group a teammate is live in (or starts one), Join picks one
  (peer to peer). Being live covers every plugin on that team copy: one Start live puts
  them all in the group, a plugin switched to the copy or enabled later joins on its own,
  closing a tab leaves nothing, and Leave takes them all out. When the host leaves,
  whoever has been in the group longest leads it; someone left alone drops out of Live but
  keeps syncing. While a plugin is live its copy
  syncs every few seconds, and each change a member commits makes the group pull it at
  once (`plugin_scope_changed` reason `synced`, as above). Each change also goes to the
  group the moment it is written, and **the group syncs through its leader**: the leader
  sends everyone's changes to source control, each named for who made it (and their ducky),
  the others hand theirs to it while it is in reach, and leaving the group goes back to
  syncing on your own. Docs up to 36 KB (files up to 27 KB) travel live; bigger ones go
  with sync at once and the group pulls them. `"data_live": false` in `plugin.json` keeps a
  plugin's changes on sync only. A folder plugin's pages reload for a teammate's pull,
  except while this person presses or types in them. With no plugin code, every
  panel page then shows members' pointers, names and selections (`data-live-id` on
  elements people point at keeps them on the same thing), and **anything someone presses
  or types in is held for them**: for every teammate on the same screen that control greys
  out with their name until they let go. **The group shares one screen, through its
  leader**: tab clicks (`role="tab"`, `a[href^="#"]`, or any control marked
  `data-live-nav`), scrolling and the screen people are on go both ways (a member's go to
  the leader, which does them and tells everyone, each page once). A page that shows
  different things at one address names its screen with `data-live-view` on `<body>`
  (`<record id>:<tab>`), so a hold never greys out a control on another record and a
  pointer only shows to people on the same screen; when the group moves, the page gets the
  window event `ducky-live-goto` (`detail.view` = that `data-live-view`): open that record
  and tab (check it first). Pictures and videos that are still syncing show a placeholder
  and load when they arrive.
  The plugin's own **components share their state with the group** (a player playing
  from 12 s, the picked clip, a panel open): a panel `live.share({key, value})` (JSON up to
  8 KB, null drops it; 64 keys), every page gets `live.shared {key, value, by}` and reads
  them all with `live.shared`; a backend uses `api.live.share(key, value)`, `.shared()`
  and `.on_shared(fn)`. One value per key for the whole group, ordered by the leader, so
  everyone ends on the same one; it shows on the PC that set it at once. For what each
  person is doing (a 3D camera so people see each other fly around, the tool in hand)
  each PC sets its own **live state**: a panel `live.setMyState({state})` as often as it
  changes (an object up to 4 KB, latest wins), and every groupmate's panels get
  `live.memberState {from, state}` (null: they left) and read all of them with
  `live.memberStates`; a backend uses `api.live.set_my_state`, `.member_states()` and
  `.on_member_state(fn)`. Someone who joins later gets both within 5 s. For one-off events
  (a drag ended) a backend uses `api.live.send(topic, data)` and `api.live.on(topic,
  callback)`, a panel `live.send` (see `ai_plugins`). Shared values and live states are
  never kept: what matters after the group goes is data, through `api.data` (the commit is
  the record).
- `ducky_team_status(plugin)` says who is in the copy and what they do, what is locked,
  what waits to go out or come in, and the conflicts. `ducky_plugin_data_scope` gives
  the same for a shared copy.

## The plugin's own folders (`team_folders`)

Only when files must stay real files on disk (another program reads them, a scene
folder, big binaries) declare the folder in `plugin.json`; the host shares it with
the team like `api.data`, with no plugin code:

```json
"team_folders": [
  {"id": "scenes", "root": "{LOCALAPPDATA}/UEFN-Ducky/<id>/scenes", "unit": "*", "lock": "unit",
   "last": ["scene.json"], "ignore": ["*/scene.prev.json", "**/*.tmp"], "after_pull": "reload"}
]
```

- `root`: under `{LOCALAPPDATA}/UEFN-Ducky/<id>/`. **Never inside the island folder**
  (`{PROJECT}/…`): UEFN revision control already carries it, so the host won't share it
  with a team, and a Store build refuses it. Per-island files go in one subfolder per
  island named by the lowercased `mount` from `ducky_get_local_project`
  (`api.call_tool("ducky_get_local_project")`): the same on every PC. Never the path or
  `slug`, which differ per PC.
- `unit` (what locks and presence are about): `*` each top-level folder and loose file
  (one per island, scene, …); `.` the whole folder; `file` every file.
- `lock`: `unit` (the first change takes the unit's lock) or `none`. `on_locked`:
  `readonly` (default) or `quarantine` (writes are kept, never sent; the bar offers
  Keep mine as a copy or Discard mine).
- `last`: globs relative to the unit, written after its other files (the file that says
  the unit is complete). `ignore`: globs relative to `root`, never shared (backups,
  temp files). `merge`: globs relative to the unit; such JSON files merge three ways
  instead of making the whole unit a conflict (≤ 32 MiB).
- `after_pull`: `notify` or `reload` (the plugin reloads on the pulled files).
- Write each file to a temp name and rename it into place: a unit goes out 3 s after its
  last change with no `*.tmp` left.
- The bar says why a folder can't be shared: a network or non-NTFS drive, or OneDrive.

## Sensitive data

`api.data.put(key, value, sensitive=True)` keeps a doc on this PC in the account's
own scope and **never syncs it**, whatever scope the plugin uses. Use it for
personal tokens, private notes, anything that must not leave this PC.

- Read it with `get(key, sensitive=True)`. `keys()` and `items()` never list it.
- Delete it with `api.data.personal().delete(key)` (plain `delete` acts on the
  active scope).
- API keys are not data: declare a `secret` setting (`secret_keys` +
  `settings.sections`) and read it with `get_key`; see `plugin_examples_tools`.

## Don't

- Don't write files with `open()`, `Path.write_*`, `shutil`, `sqlite3` or a folder of
  your own: teammates never get them (`ducky_plugin_validate` warns). The exceptions:
  a declared `team_folders` folder; this PC's caches in `api.local_dir(name)`; files a
  workflow node or image generator makes in the `artifact_dir` / `output_dir` Ducky
  passes in. Project files go through `api.call_tool("workspace_write_file", …)`.
- Don't keep anything inside the island folder (`.ducky/…` and the like): UEFN revision
  control carries it, so it never reaches a team through the host.
- Don't keep records in `prefs` (small UI choices only) or in page memory (panels
  reload when moved to a floating window).
- Don't read another plugin's data; call its MCP tools instead (`api.call_tool`).
