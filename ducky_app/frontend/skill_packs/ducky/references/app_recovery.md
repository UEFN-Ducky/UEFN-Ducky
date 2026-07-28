---
description: "Recovering the app — listener offline, wrong project, stale tools after an update"
metadata:
  order: 1
  label: "App recovery"
  default_enabled: false
  load_condition: "The panel/listener is offline, the wrong project responds, or tools are empty after an EXE update"
---

## App recovery

| Symptom | Fix |
|---------|-----|
| `ping` fails / listener offline | User: open the UEFN project; the panel should show "Listener online". If the panel is closed, launch `UEFN-Ducky.exe`. |
| Wrong project responding | Panel Settings → project root; the listener follows the open UEFN project. |
| Empty tool results after an EXE update | Launch the new EXE once (or reconnect MCP) — `ship_newest_everywhere` refreshes listener + skills + IDE MCP configs. Then start a **new chat**. |
| Stale MCP after reinstall | Open the new EXE once — it rewrites Cursor/Claude/Antigravity `mcp.json` to itself. |

For MCP-side recovery (STOP stays true, tools hang after `reload_listener`), see
the **UEFN MCP** skill's `troubleshooting` reference.
