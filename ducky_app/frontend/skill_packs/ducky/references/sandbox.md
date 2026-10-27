---
description: "Sandbox: a private workspace per chat to download, install, build, run and study programs, verify a change end to end (a Ducky test build included), and show sandbox web apps in the Browser tab"
metadata:
  order: 4
  label: "Sandbox: try, build, run, verify"
  default_enabled: false
  load_condition: "Trying a library, program or repo; reproducing a bug; building or running something; verifying a change before commit; testing a build of Ducky or a plugin; anything that needs a scratch folder"
---

## What a sandbox is

Every chat has its own sandbox: `sandbox_open()` shows it. It has a `work` folder (clones,
worktrees, builds), `downloads`, and its own profile folders: programs started in it get
their own `%USERPROFILE%`, `%APPDATA%`, `%LOCALAPPDATA%`, `%TEMP%` and pip/npm/uv caches, so
nothing lands in the person's real profile. Ducky's file tools read and write it without
approval cards; nothing in it shows as a project change.

Use it for ALL scratch work: never the project, never the OS temp folder.

- Commands and programs ask first (one card), unless the chat chose "Allow sandbox commands".
- Never runs in a sandbox: admin, system installers (msi, winget), registry/PATH changes,
  scheduled tasks, releases/publishes/deploys, git push. A sandbox ends at a report; commit
  and push happen in the project, deploys are the owner's.
- It is a tidy workspace, not a jail: don't run programs you don't trust.

## Tools

- `sandbox_setup(runtime)`: node, python (3.12 via uv), uv, git. Portable, pinned, checked; once per PC.
- `sandbox_clone(repo, ref)`: a public repo into `work`. `sandbox_fetch(url, sha256)`: one file into `downloads`.
- `sandbox_worktree(repo_path, commit, copy=[...], copy_from=...)`: a clean checkout of one of
  the person's projects at a commit (no other chat's uncommitted files), plus listed ignored files.
- `sandbox_run(command)`: a command that finishes (install, build, test) in the sandbox terminal.
- `sandbox_launch(program, args, ports=[...], env={...}, files={...})`: something that keeps
  running (a server, an app, a test Ducky). `ports` names env vars that each get a free port.
  `files` writes settings files first. Stopped with its whole tree by `sandbox_stop` or when Ducky closes.
- `sandbox_look(pid, capture=true)`: alive, memory, CPU, listening ports, output, windows, a picture of its window.
- `sandbox_http(port, method, path, body)`: its own local web API. `sandbox_mcp(command|url, calls=[...])`: its MCP tools.
- `browser_open("localhost:<port>")` shows a sandbox web app in Ducky's Browser tab;
  `browser_capture()` saves a picture of it, `browser_read()` its text (sandbox pages only).
- `sandbox_stop(pid)`, `sandbox_clear()`: stop, or delete everything (worktrees removed too).

Open every picture with `view_image(path)` and describe what you see.

## Verify a change before you push

"Done" means the tests that cover the change passed. Run them first; only when no test can show
the change works (a web app or UI with no test, an API with no client test), check it live:

1. Commit nothing yet. `sandbox_worktree(repo_path=<project>, commit="HEAD")` if the change is
   committed locally, else build from the project itself only when it is the one thing changed.
2. Install and build in the sandbox: `sandbox_run("npm ci && npm run build")`, `sandbox_run("uv venv && uv pip install -e .")`.
3. Start it: `sandbox_launch("npm.cmd", ["run", "dev", "--", "--port", "%PORT%"], ports=["PORT"], cwd="<worktree>")`
   (most dev servers read `PORT`; check the project's docs).
4. Check it the way a person would: `browser_open("localhost:<port>")` + `browser_capture()` +
   `browser_read()` for web apps; `sandbox_look(pid, capture=true)` for desktop apps;
   `sandbox_http` / `sandbox_mcp` for APIs; the test suite with `sandbox_run`.
5. Report what you checked, each with its evidence (the picture, the output lines).
6. `sandbox_stop()`. Then commit (named files only) and push from the project. Never deploy.

## Ducky test build (UEFN Ducky itself)

Only for window, startup or packaging changes that tests can't cover: a build takes 15-30 min.
Run it in the background with a time limit and never poll it in a loop. To check such a change
in a real app, beside the person's running Ducky:

1. `sandbox_worktree(repo_path="<UEFN-Ducky checkout>", commit="HEAD", name="ducky-test",
   copy_from="<the release checkout, e.g. ducky-rel>", copy=["build/ffmpeg-bundle", "build/search-model",
   "ducky_app/backend/uefn_plugins/store_signing_key.json", "ducky_app/frontend/ui_web/web/public/draco",
   "ducky_app/frontend/ui_web/web/public/verse-workflow", "ducky_app/frontend/ui_web/web/src/verse-editor/lang"])`.
2. `sandbox_run("cd ducky-test/ducky_app/frontend/ui_web/web && npm ci --prefer-offline", timeout_s=900)`, then
   `sandbox_run("cd ducky-test && <the repo's .venv python> build/build_exes.py", timeout_s=3000)` (15-20 min; it bumps the
   version only inside the worktree).
3. Plugins under test: copy their folders into `appdata/Local/UEFN-Ducky/uefn_plugins/<id>/` (workspace tools).
4. `sandbox_launch("ducky-test/dist/UEFN-Ducky-<v>/UEFN-Ducky.exe", ports=["UEFN_DUCKY_PANEL_PORT", "UEFN_DUCKY_PORT"],
   env={"DUCKY_SKIP_LICENSE_GATE": "1"}, files={"appdata/Local/UEFN-Ducky/panel_settings.json":
   '{"enabled_uefn_plugins": [...], "trusted_local_uefn_plugins": [...], "web_access": "on", "first_run_setup": "done",
   "welcome_ducky_offered": true, "starter_llm_gateways_seeded": true}'})`. Its panel API is on
   UEFN_DUCKY_PANEL_PORT minus one; its window opens on the person's screen.
5. Make a chat: `sandbox_http(<panel port - 1>, "POST", "__panel_api/create_conversation", {"args": {"folder_id": ""}})`.
   Drive it like an IDE agent does: `sandbox_mcp(command="ducky-test/dist/UEFN-Ducky-<v>/UEFN-Ducky-Bridge.exe",
   args=["bridge", "--port", "<UEFN_DUCKY_PORT>"], env={"UEFN_DUCKY_PANEL_PORT": "<port>", "DUCKY_CONV_ID": "<chat id>"},
   calls=[{"tool": "ducky_rename_self", "arguments": {"title": "Test"}}, {"tool": "<the tool you changed>", ...}])`
   (a new chat must call ducky_rename_self first). A plugin changed: copy the new file in, then
   `sandbox_http(..., "__panel_api/reload_uefn_plugin", {"args": {"plugin_id": "<id>"}})`.
6. `sandbox_look(pid, capture=true)` for its window. Report with evidence.
7. Stop it ONLY with `sandbox_stop(pid)`: never its panel API `exit_all` (that closes every Ducky,
   the person's too). Then `sandbox_clear()`.

## Learn a program

`sandbox_setup` the runtime → `sandbox_clone` / `sandbox_fetch` → read its README with the
file tools → `sandbox_run` the install → `sandbox_run("<prog> --help")` → `sandbox_launch` it →
`sandbox_look` / `browser_open` + `browser_capture` / `sandbox_http` → write down what it does,
how it is configured, and what surprised you (`project_memory_append` when it matters later) →
`sandbox_stop` → `sandbox_clear` when the chat is done with it.
