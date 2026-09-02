# UEFN-Ducky

**An AI workspace for Unreal Editor for Fortnite — chat, code, and build your island without leaving the app.**

UEFN-Ducky is a desktop app where AI agents ("duckies") build alongside you: they place and
wire devices, write and compile Verse, author materials and Niagara effects, lay out levels,
model in Blender, and test the result — while you watch it happen in the built-in editor.
Everything it can do is a plugin you install from the Store, so you ship with exactly the
toolset you want.

> **Not affiliated with Epic Games.** This is a community tool that automates *local*
> workflows against a UEFN install you already have. "Unreal", "UEFN", "Verse", and
> "Fortnite" are trademarks of Epic Games, Inc. See [LICENSE](LICENSE) and
> [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).

> **No official cryptocurrency or token.** There is no official UEFN Ducky or DuckyOS coin.
> Tokens on pump.fun, bump.fun, or similar that use our name, logo, GitHub, or contributor
> list are unofficial. The Contributors graph is a commit list, **not founders**.
> **AnasInno** is a pull-request author only — not a founder, not an officer, not authorized
> to claim creator fees. See [UNOFFICIAL_TOKENS.md](UNOFFICIAL_TOKENS.md).
> Official support is Patreon and [uefnducky.org](https://uefnducky.org) — not crypto.

> **Platform:** Windows. macOS is not bundled yet.

---

## Features

### Duckies — your agents

- **Duckies** are agent profiles you create and keep: each one gets its own model, coding
  agent, personality, voice, skills, tools, and memory.
- **Groups** turn duckies into a team — a leader, members with their own models, nested
  sub-groups, and shared context, all in one conversation.
- **Ask / Plan / Agent** modes per message, with a thinking-effort dial for extended
  reasoning and a prompt queue so you can stack follow-ups while a turn is running.
- **Plans** give long jobs a visible roadmap: a live progress bar in chat, a full plan
  editor in its own tab, and reusable plan templates.
- **Memory** at three levels — global, per-ducky, and per-project — so an agent
  remembers your project's conventions instead of relearning them.
- **Skills** are packs of domain knowledge (UEFN, Verse, level design, VFX, Blender…) you
  attach per ducky and author yourself in the Skill Pack Studio.
- **Voice**: talk to a ducky live, or have replies read back — with local offline voices or
  ElevenLabs.
- **Snip** any region of your screen straight into the chat, or drop in files and images.

### The workspace

- Split editor tabs, drag-to-dock side panels, and focus windows that pop a single chat,
  file, plan, or terminal out on its own.
- **Duckies**, **Content**, **Outline**, **History**, and **Tester** panels around a
  central editor area, plus Quick Open (`Ctrl+P`) across your project.
- Embedded **terminals** (bash or PowerShell) that you and your duckies share, with
  approval prompts before an agent runs anything.
- Project switching with recent projects, and completion alerts with sounds when a
  long-running turn finishes.

### Verse editor

- Full Verse editing with syntax highlighting, completions, hover, go-to-definition, and
  formatting powered by Epic's own Verse tooling.
- **Problems**, **Build**, and **Push** in the header — compile errors are clickable, and
  changes go to UEFN without leaving the app.
- **New Verse class** templates (plus your own saved templates), per-file version history
  you can preview and restore, and an outline of every symbol.
- **Ask a ducky** on any selection, and watch agent edits replay in the editor as they land.
- Vim keybindings available as a plugin.

### Settings

Providers and API keys, coding agents, MCP servers, appearance themes, audio, skills,
memory, plans, and the Store — all in one place. **Apply** wires UEFN-Ducky into Cursor,
Claude, or Antigravity in a click, so the same tools work from your IDE too.

Appearance goes deep: themes are editable profiles covering the app shell, sidebar, chat,
terminal, buttons, semantic colors, and Verse syntax — and the Store ships ready-made ones.

---

## Plugins

Everything below installs, updates, and uninstalls from **Settings → Plugins**, backed by
the [UEFN Ducky Store](https://uefnducky.org). Each plugin brings its tools *and* the skill
that teaches your duckies how to use them properly.

### UEFN creation

| Plugin | What it adds |
|--------|--------------|
| **UEFN** | The core editor surface: actors, assets, creative devices, data tables, editor control, introspection, project memory |
| **UEFN Verse** | Verse digests and API lookup, device wiring, compile and errors, UMG widget blueprints, and system template packs (Player Core, Economy, Progression…) |
| **UEFN Level Design** | Spatial layout, landscapes, blockout presets, worldgen, foliage, PCG, Fort actors |
| **UEFN Materials** | Create and edit materials, instances, graphs, and flags; assign them to meshes |
| **UEFN Niagara** | Assemble particle systems emitter by emitter — stock modules, renderers, project particle meshes, live parameters |
| **UEFN Scene Graph** | Entities, components, and prefabs; component lifecycle, movement, and itemization |
| **UEFN Animation** | IK retargeting, skeleton sockets, Level Sequence and AnimSequence authoring, bakes, and Animated Mesh playback |
| **MetaHuman** | Creator round-trips, Mesh to MetaHuman, UEFN Export assembly, and NPC spawning |
| **UEFN Modeling** | Static mesh info and collision |
| **UEFN Physics** | Physics Beta — project setup, FortPhysics props, Verse impulses, and volume prop events |
| **UEFN Virtual Pointer** | Cross-platform pointer input via Verse Enhanced Input: touch mapping, select, zoom, swipes, pinch |
| **UEFN Testing** | Device-graph simulation, Verse harness tests, and session probes, plus the Tester dock |
| **UAsset Preview** | Preview `.uasset` / `.umap` assets and standalone 3D files right in the panel |

### Bring in art from elsewhere

| Plugin | What it adds |
|--------|--------------|
| **Blender** | Drive Blender from chat — model, rig, texture, and export to UEFN; the addon installs itself |
| **Meshy** | Text/image-to-3D, free community models, remesh, retexture, auto-rig, and animate |
| **3D AI Studio** | Text/image-to-3D, image generation, and mesh repair/convert/optimize (Tripo, TRELLIS, Tencent) |
| **Google Drive** | Pull models, textures, and audio from one read-only Drive folder into UEFN |
| **UNITY MCP** | Control the Unity Editor from Ducky, with the MCP server installed for you |

### Models and voices

| Plugin | What it adds |
|--------|--------------|
| **Anthropic**, **OpenAI**, **Google**, **Cursor**, **SpaceXAI**, **Kimi** | API keys plus their coding agents (Claude Code, Codex, Gemini CLI, Cursor Agent) under Settings → LLMs |
| **Ollama** | Point at your own local server and run models entirely on your machine |
| **Pipe** | Free neural voices that run 100% offline on your PC |
| **ElevenLabs** | Premium voices, assignable per ducky |

### In the app

| Plugin | What it adds |
|--------|--------------|
| **Web Browser** | A real Chromium tab inside the app — docs, dashboards, and AI-driven navigation |
| **Discord** | Bot chat, `!ducky` commands, and server-admin tools for your agents |
| **Ducky Account** | Sign in, manage team presence, link the desktop app to the cloud |
| **Translation** | Live-translate the whole UI into languages you add, using the model you pick |
| **Vim** | Vim keybindings in the Verse editors |

### Themes and fun

**Light**, **Hacker** (Matrix rain), **Galaxy Craft** (Terran command console), and
**Warcraft** (carved gold and parchment) reskin the entire app. **Duck-Tac-Toe** plays
tic-tac-toe with your ducky on a live board inside the chat.

---

## Install

Grab the installer from [uefnducky.org](https://uefnducky.org), or build it yourself below.
You need **Windows** and a **UEFN** install from the Epic Games Launcher; the app's
walkthrough handles connecting the two on first run, and updates arrive in-app.

The Verse editor uses the grammar, snippets, and language server that ship with UEFN.
**UEFN-Ducky bundles none of Epic's assets** — it reads them from your local install on
first launch. If it can't find UEFN:

```bash
py scripts/extract_vscode_ext.py            # auto-detects your UEFN install
py scripts/extract_vscode_ext.py --extension "C:\path\to\extracted\verse"
```

## Build it yourself

No prebuilt binaries are committed here.

```bash
py -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt pyinstaller
.venv\Scripts\python build/build_exes.py          # -> dist/UEFN-Ducky.exe
```

> **Build in a clean virtualenv, not your system Python.** PyInstaller bundles everything
> it can import, so a global `site-packages` full of unrelated libraries silently adds tens
> of MB to the EXE.

**Windows installer** (needs [Inno Setup 6](https://jrsoftware.org/isinfo.php)):

```bash
powershell -ExecutionPolicy Bypass -File release/installer/make_release_installer.ps1
```
→ `dist/UEFN-Ducky-Setup-<version>.exe`

**Portable zip:** `powershell -ExecutionPolicy Bypass -File release/portable/make_release_zip.ps1`
**Everything at once:** `powershell -ExecutionPolicy Bypass -File release/build_all.ps1 [-Zip] [-Sign]`

Code signing is optional and off by default; unsigned builds trigger a SmartScreen warning
on first run. Set `DUCKY_WINDOWS_PFX` + `DUCKY_WINDOWS_PFX_PASSWORD` and run
`py release/sign_windows.py dist/UEFN-Ducky-Setup-<version>.exe`.

**Tests:**

```bash
py -m pip install -r requirements-dev.txt
py -m pytest
```

## Contributing

Contributions welcome — see [CONTRIBUTING.md](CONTRIBUTING.md).

## License

[Ducky Source-Available License v1.0](LICENSE) — source-available, not OSI open source.
You may read, study, run official builds, and contribute. You may not redistribute,
resell, or reuse the code in other products, and the "UEFN-Ducky" name, logo, and duck
mascot remain with Mindful Path Company, LLC. See [LICENSE](LICENSE) for the full text.

Not affiliated with Epic Games; UEFN and its bundled tooling are governed by Epic's own
terms — see [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
