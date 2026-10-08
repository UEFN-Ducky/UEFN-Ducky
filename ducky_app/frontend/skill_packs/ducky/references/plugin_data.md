---
description: "Plugin data and teams: api.data docs and files, Local or exactly one team, sensitive data, encryption, read-only and lost access"
metadata:
  order: 4
  label: "Plugins: data and teams"
  default_enabled: false
  load_condition: "A plugin stores records, files or settings, or the user asks about sharing plugin data with a team"
---

# Plugin data and teams

Every plugin stores its data through the host: `api.data` in the backend, `data.*` /
`files.*` in panels. Never a folder, file, SQLite database or `%LOCALAPPDATA%` path
you pick: the host keeps each plugin's data per signed-in account and per scope,
encrypted on disk, and syncs team copies.

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
| `scope()` | `scope.get` → `{scope}` | `{kind, label, teamId, readOnly}`: whose copy this is |

- **One doc per entity**: `card.pip`, `pack.starter`, `run.2026-10-08`. Never one
  big `db.json`: two people editing different cards must not overwrite each other.
  Within one doc the last write wins.
- Doc keys `[a-z0-9._-]` up to 128 characters; file paths `[a-z0-9._/-]` up to 256,
  no `..`, no leading dot. Sanitize ids that come from users before using them in keys.
- `put` returns `changed: false` when nothing changed, so it is cheap to call.

## Local or exactly one team

- The **user** picks, per plugin, whether its data is **Local** (this account on this
  PC) or shared with **one** team: Plugins → the plugin → Data. A plugin never
  decides, never mixes the two, and needs no code for either.
- `ducky_plugin_data_scope(id, scope="")`: empty `scope` tells you which copy a plugin
  uses; `"local"` or a team id switches it. Switch only when the user asks: it asks
  them first, nothing is copied, open panels are told, the new copy syncs, and the
  plugin restarts on it with its open panels reloaded.
- Panels get `plugin_scope_changed` when the scope switches or a sync brings new data:
  re-read your docs. The backend reads the scope fresh on every call.
- `prefs.*` and `cache.*` follow the scope too: a team copy has its own prefs and cache
  on this PC (never synced), locked and unlocked with it.

## Team copies

- Synced with the team through the Store. Each item is encrypted with that team's
  own key before it leaves the PC; the server stores only encrypted bytes. Teams
  never see each other's data.
- A team copy can be **read-only** (the team's storage plan is paused). Writes then
  raise `PermissionError` with a message for the user. Catch it in tools and return
  `{"ok": False, "error": str(exc)}`.
- **Losing access** to the team locks its copy on this PC: reads come back empty and
  writes fail until access returns, then everything unlocks as it was. After a week
  without access the copy on this PC is deleted (never the server's). A plugin
  published to that team pauses too, and resumes when access returns.
- Whose data the user is looking at: the panel's scope bar shows it, and the host adds
  `scope` (`local` or `team <name>`) to every dict a tool of a data-using plugin
  returns. Say it in your reply when it matters.

## Sensitive data

`api.data.put(key, value, sensitive=True)` keeps a doc on this PC in the account's
own scope and **never syncs it**, whatever scope the plugin uses. Use it for
personal tokens, private notes, anything that must not reach a team.

- Read it with `get(key, sensitive=True)`. `keys()` and `items()` never list it.
- Delete it with `api.data.personal().delete(key)` (plain `delete` acts on the
  active scope).
- API keys are not data: declare a `secret` setting (`secret_keys` +
  `settings.sections`) and read it with `get_key`; see `plugin_examples_tools`.

## Don't

- Don't write files with `open()`, `Path.write_*`, `sqlite3` or a folder of your own.
  The one exception: files a workflow node or image generator makes go in the
  `artifact_dir` / `output_dir` Ducky passes in. Project files go through
  `api.call_tool("workspace_write_file", …)`.
- Don't keep records in `prefs` (small UI choices only) or in page memory (panels
  reload when moved to a floating window).
- Don't read another plugin's data; call its MCP tools instead (`api.call_tool`).
