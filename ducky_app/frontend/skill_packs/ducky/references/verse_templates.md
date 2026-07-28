# Custom Verse templates (user / AI)

Reusable Verse scaffolds stored in AppData — **not** Store plugin packs.

Path: `%LOCALAPPDATA%/UEFN-Ducky/verse_templates/<slug>.json`

## Do not touch Store templates

Plugin packs from `uefn-plugin-verse` (and other plugins) use:

- `verse_template_list` / `verse_template_get` / `verse_template_apply`

Those are read-only apply tools. **Never** try to edit Store/plugin template source with
`ducky_verse_template_*`.

## Custom tools (`ducky_verse_template_*`)

| Tool | Purpose |
|------|---------|
| `ducky_verse_template_list` | List custom templates |
| `ducky_verse_template_get` | Read full content / files |
| `ducky_verse_template_save` | Create or update (`template_id` = `custom:…`) |
| `ducky_verse_template_delete` | Delete (`confirm=true`) |
| `ducky_verse_template_apply` | Write into the open project |

Prefer **save a template + apply** over pasting one-off Verse into a new file when the
user will reuse the scaffold.

## Single file

```
ducky_verse_template_save(name="My Device", content="using { /Verse.org/Simulation }\n…")
```

## Multi-file system pack (same shape as plugin `verse.templates`)

```
ducky_verse_template_save(
  name="PlayerCore",
  folder="PlayerCore",
  files_json='[{"path":"player_api.verse","content":"…"},{"path":"impl/player.verse","content":"…"}]'
)
```

- `folder`: one segment under `Content/Verse` when applied
- `path`: relative to that folder; nested dirs ok
- Update: pass `template_id` from list/get

## UI

New file → **Add custom template** → Single file or **Multi-file system**.
Pencil on a custom row edits it. Store/plugin rows have no edit/delete.
