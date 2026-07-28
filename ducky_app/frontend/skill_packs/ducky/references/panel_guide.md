---
description: "Control panel features — IDE hookup, Skills studio, MCP plugins, chats"
metadata:
  order: 2
  label: "Control panel guide"
  default_enabled: false
  load_condition: "User asks about the UEFN-Ducky app itself (setup, skills studio, panel features)"
---

## Control panel guide

**IDE hookup:** Settings → MCP → Apply merges the `uefn` MCP server into the
IDE's global config (Cursor `~/.cursor/mcp.json`, Claude Desktop
`claude_desktop_config.json`, Antigravity `mcp_config.json`). Applying also
deploys every skill pack as a standard skill folder to `~/.claude/skills/`,
`~/.cursor/skills/`, and `<config dir>/skills/`.

**Skills studio (Settings → Skills):** packs and their files in a sidebar —
`SKILL.md` is the always-on core; `references/*.md` load on demand. Content tab
edits the markdown (Ctrl+S saves); Details tab edits label, description,
enabled-by-default, and load condition. Packs export/import as
`.ducky-skill-pack` zips.

**Per-chat toggles:** each chat can enable/disable packs and individual
reference files (the + button / Skills popover). Defaults come from Settings.

**MCP plugins:** extra MCP servers (and built-in tool groups) can be toggled
globally or per chat in Settings → MCP plugins.

**Also in the panel:** project file tree with a Verse editor (diagnostics,
compile), terminals, and multi-chat sidebar with folders.

**AI-made plugins:** chat duckies can build desktop plugins (themes, panels,
MCP tools) with `ducky_plugin_*`. Full guide:
`skill_read_subskill("ducky", "ai_plugins")`.
