"""How long a shared-MCP tool call may run before it is stopped (see shared_mcp._watched).

Tests patch these names on this module: ``call_limit`` reads them at call time.
"""

from __future__ import annotations

import threading
from typing import Any, Callable

# Answered from Ducky's own tool catalog and skill index: a call past this never answers,
# so it is stopped and the agent hears why. Oct 18 2026: a user's agent sat 29 minutes on
# ducky_find_tools; agents (Claude Code) wait on an MCP call with no limit of their own.
QUICK_TOOLS = frozenset({"ducky_get_tools", "ducky_find_tools", "skill_search", "skill_read_subskill"})
QUICK_TOOL_LIMIT_S = 60.0
# Reads that answer in milliseconds. Nov 4 2026: 13 of these hung until Claude Code's
# 1800 s abort; past this limit the agent hears why instead of waiting half an hour.
QUICK_READS = frozenset({
    "ducky_get_plan", "ducky_health", "ducky_get_status", "ducky_agent_list", "ducky_agent_transcript",
    "ducky_agent_inbox", "ducky_list_chats", "ducky_read_chat", "ducky_terminal_read_output",
    "ducky_terminal_list", "ducky_get_errors", "workspace_read_file", "workspace_read_files",
    "workspace_search", "workspace_find", "workspace_file_outline", "workspace_list_dir", "workspace_tree",
    "workspace_git", "project_memory_list", "project_memory_get",
})
QUICK_READ_LIMIT_S = 30.0
# A Store plugin or nested MCP tool run through ducky_call_tool: Codex waits on an MCP call
# forever, and 9 of 15 stuck calls on Oct 31 2026 were these. Long enough for a render or
# a 3D generation; a tool that waits by design (WAITS_BY_DESIGN) has no limit.
# ponytail: one limit for every plugin tool; a per-tool limit if a real tool needs longer.
PLUGIN_CALL_LIMIT_S = 900.0
# Epic's UEFN MCP (unreal__*) has no limit here: client_pool gives each UEFN call its own
# deadline (epic_call_limit) and answers with why, without cancelling the editor's work.
# Nov 9 2026: 38 unreal__call_tool calls were agent.stuck past 5 min with no answer.
EPIC_CALL_LIMIT_S = 120.0
EPIC_HEAVY_LIMIT_S = 300.0
# Ducky-listener tools that drive one long editor operation. Nov 11 2026: import_asset sat
# past 5 min 10 times, execute_python 6, take_high_res_screenshot 2, with no word to the
# agent. They get the heavy Epic deadline: at it the agent hears why (stall_reason) and that
# the editor's work goes on, so it checks the result instead of sending the job again.
EDITOR_HEAVY_TOOLS = frozenset({"import_asset", "execute_python", "take_high_res_screenshot"})
# A Verse build answers within this, or the agent hears it is still building / behind a popup.
# Nov 9 2026: 26 Verse build/check calls ran past 5 min.
VERSE_BUILD_LIMIT_S = 180.0
VERSE_POPUP_CHECK_S = 60.0
# Calls that wait by design (on the person, or a command / workflow / window with its own
# timeout): still recorded where they wait, but not counted as an anonymous agent.stuck,
# which said "stuck" 117 times a day for questions and test suites (Oct 25 2026).
WAITS_BY_DESIGN = frozenset({
    "ducky_ask_user", "ducky_terminal_run", "sandbox_run", "run_workflow", "run_workflow_node",
    "ducky_wait_uefn_ready", "uefn_wait_window", "uefn_logs_wait", "fortnite_client_wait", "playtester_wait",
})


def call_limit(name: str, inner: str) -> float | None:
    """Seconds a call may take before it is stopped; None = no limit (it may wait by design)."""
    if inner in QUICK_READS:
        return QUICK_READ_LIMIT_S
    if inner in QUICK_TOOLS:
        return QUICK_TOOL_LIMIT_S
    if inner in EDITOR_HEAVY_TOOLS:
        return EPIC_HEAVY_LIMIT_S
    if inner.startswith("unreal__"):
        return None
    if name == "ducky_call_tool" and inner != name and inner not in WAITS_BY_DESIGN:
        return PLUGIN_CALL_LIMIT_S
    return None


def epic_call_limit(heavy: bool) -> float:
    """Seconds one Epic UEFN call may run before the agent hears why it has not answered."""
    return EPIC_HEAVY_LIMIT_S if heavy else EPIC_CALL_LIMIT_S


def blocking_popup() -> dict[str, Any] | None:
    """The first UEFN popup that holds the editor, else None. Never raises."""
    try:
        from backend.tools.core.uefn_popups import list_popups

        return next((p for p in list_popups() if p.get("blocking")), None)
    except Exception:  # noqa: BLE001 — a failed look is not a popup
        return None


def _editor_answers() -> bool:
    """True when the listener's game thread answers a ping within a few seconds."""
    try:
        from backend.bridge import send_command

        send_command("ping", {}, timeout=5.0)
        return True
    except Exception:  # noqa: BLE001
        return False


def stall_reason(op: str, limit: float) -> dict[str, Any]:
    """Why an editor call has not answered within ``limit``: popup | editor_busy | no_answer."""
    popup = blocking_popup()
    if popup:
        return {"ok": False, "reason": "popup", "hwnd": hex(int(popup.get("hwnd") or 0)),
                "title": str(popup.get("title") or ""),
                "message": f"{op} is held by a UEFN popup: call uefn_popups, then uefn_popup_press(hwnd, n)."}
    if not _editor_answers():
        return {"ok": False, "reason": "editor_busy",
                "message": f"{op} did not answer in {limit:.0f} s and UEFN's game thread is busy: "
                           "its work goes on. Wait, then check its result before calling again."}
    return {"ok": False, "reason": "no_answer",
            "message": f"{op} did not answer in {limit:.0f} s although the editor answers: "
                       "its work may go on. Check the result before calling it again."}


class Job:
    """One blocking call run on a daemon thread, so its caller can stop waiting without
    stopping it (the editor op always runs to its end)."""

    def __init__(self, fn: Callable[[], Any]) -> None:
        self.done = threading.Event()
        self.result: Any = None
        self.error: BaseException | None = None
        threading.Thread(target=self._run, args=(fn,), name="editor-job", daemon=True).start()

    def _run(self, fn: Callable[[], Any]) -> None:
        try:
            self.result = fn()
        except BaseException as exc:  # noqa: BLE001 — handed to the waiter
            self.error = exc
        finally:
            self.done.set()


def wait_job(job: Job, limit: float, check_every: float,
             popup: Callable[[], dict[str, Any] | None] = blocking_popup) -> tuple[str, Any]:
    """Wait up to ``limit`` s, looking for a blocking popup every ``check_every`` s and at the
    end. Returns ("done", result), ("popup", popup) or ("timeout", None); re-raises the job's error."""
    waited = 0.0
    while True:
        step = min(check_every, limit - waited)
        if job.done.wait(max(step, 0.0)):
            if job.error is not None:
                raise job.error
            return "done", job.result
        waited += step
        seen = popup()
        if seen:
            return "popup", seen
        if waited >= limit:
            return "timeout", None


def timed_out_message(inner: str, limit: float) -> str:
    """What the agent hears when a call is stopped at its limit. An editor job is not stopped,
    only no longer waited for: the agent hears what holds it (a popup, a busy editor)."""
    if inner in EDITOR_HEAVY_TOOLS:
        return str(stall_reason(inner, limit).get("message") or f"{inner} did not answer in {limit:.0f} s.")
    return (f"{inner} did not answer within {limit:.0f} s and was stopped. If this chat asked "
            "the person a question, tools wait for that answer first. Otherwise call it once "
            "more; if it stops again, carry on without it and tell the person.")
