# Questions, permissions and chat lifecycle

Required questions remain pending until the user submits nonempty answers to
every required question. Timeout, transport errors, panel reload, Stop and
window closure never count as approval. Multiple questions in a chat remain
queued. Unanswered questions are committed to ducky.db before being shown.
The panel replays them after UI reloads and full application restarts. Answers
are committed before waking the caller; a database error cannot grant approval.

Embedded tool dispatch waits on the broker. MCP dispatch checks the owning
panel over bounded HTTP rounds before executing another task tool, including
from separate bridge processes. A missing panel fails closed. Forbidden tools
are rejected by file protection before this check. An unsuccessful ask ends
the embedded model turn rather than feeding an error back into another step.

Ducky's question wait has no expiry. Wrapper dispatch resolves the inner tool
before choosing its timeout, so ducky_call_tool wrapping ducky_ask_user inherits
the infinite wait. Codex requires a finite tool_timeout_sec; generated config
uses 1000000000000 seconds (approximately 31,700 years), replacing 180 seconds.
The OpenAI Store adapter also generated 180 seconds on each launch. Version
1.0.48 corrects this in the canonical plugin repository. The matching source
patch is docs/patches/openai-question-waits.patch. Other
external hosts may impose their own limits; a host failure must not clear the
question or release task tools. Already-running packaged processes do not load
these source changes until an updated build starts.

General → Permissions and rules contains file protection, AI settings changes,
AI clicks, UEFN visibility, other program visibility, web access, microphone
permission and question/approval explanations. Account restrictions and strict
protection take precedence over the individual permission switches.

A new placeholder Ducky receives an instruction in the working model's first
prompt to call ducky_rename_self first with a task name. Dispatch enforces this
for Ducky tools. The tool derives the chat identity from the caller and updates
the sidebar without creating a tab. Later streaming saves retain the stored
name. Manual names and disabling automatic naming remain supported. No separate
model request generates a title.

Tool events preserve call IDs and match completions to the same call, including
repeated tool names. Finalized runs settle unmatched calls as interrupted.
Historical calls without a completion no longer imply a live process. Individual
tool cards and groups have no duplicate Stop controls; the run Stop remains.

Conflict detection uses conversation IDs to distinguish agents. A later run
in the same chat is not another agent; stale-base protection still rejects an
edit based on an obsolete read. Changes filters use profile/conversation IDs,
never display names, so identical names do not merge identities.

Focus windows retain their Win32 subclass callback for the process lifetime.
WM_NCDESTROY clears the handle state after forwarding to the original procedure;
bridge workers do not release a callback before native destruction. Close/return
requests include the window ID, so an old window cannot close a reopened tab.
Tab claims are recorded centrally before opening and delayed claim events check
the current owner before closing a local tab. Native integration coverage runs
12 hidden WinForms/WebView2 create/open/close cycles in a separate process.
Initial database WAL configuration and schema migration share the same lock.

Regression coverage includes backend/panel/test_question_gate.py,
frontend/ui_web/test_question_waiting.py, frontend/ui_web/test_permissions_settings.py,
backend/agent/test_chat_title.py, frontend/ui_web/test_checkpoint_coding_turn.py,
and the corresponding question, settings, RPC replay and tool activity UI tests.
