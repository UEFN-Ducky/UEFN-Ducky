# AI file protection

Settings → General → Permissions and rules (immediately after App Data) stores
`ai_ignore_patterns` and `ai_ignore_strict` in
PanelSettings. Only the human settings API may change them. Agent settings tools
mark both fields non-settable. New and existing installations default to strict
protection. `.env` and `.env.*` are always protected in guarded workspace tools,
including when the user explicitly chooses weaker protection.

Rules accept filename globs, project-relative paths, folders and absolute paths.
A protected folder protects its descendants. Windows matching ignores case and
trailing dots/spaces. Negation and parent traversal are refused. Checks cover
both the supplied path and its canonical target; alternate data streams and
multiply-linked files are refused. Search and listings omit denied entries.
Read, write, temporary-file creation and whole-folder move/delete operations
check before reading contents or changing disk. Attachment hydration and media
preparation also check original names, source paths, frame/transcript caches and
temporary outputs before reading, touching timestamps, copying or running ffmpeg.
Human file editing remains available.

Strict protection admits only the audited workspace functions and ask/plan
operations, through both embedded tool dispatch and MCP protocol dispatch.
Unknown tools, plugin wrappers, scripts, Git history/helpers and desktop/browser
access are refused. External coding agents are refused before adapter detection
or launch because their native tools and shells share the user's filesystem
permissions. Protection changes are refused while an agent is running; external
startup and policy saves share a lock. Workspace mutations cannot change the
installed app or its AppData, which contains enforcement settings and extensions.
Invalid or unavailable policy storage fails closed.

This is an application capability boundary, not an OS sandbox. Installed
extensions and the app host are trusted code. It cannot revoke information already
sent to a model, identify copied secrets under unrelated names, govern other
processes, or govern independently launched CLI agents. Turning strict protection
off permits filesystem bypasses and deliberately provides no isolation guarantee.

Regression coverage: `backend/workspace/test_ai_ignore.py` and
`frontend/ui_web/web/src/views/settings/AiIgnoreSection.test.tsx`. Use synthetic
secret files in isolated test AppData; never test by opening a real user's .env.
