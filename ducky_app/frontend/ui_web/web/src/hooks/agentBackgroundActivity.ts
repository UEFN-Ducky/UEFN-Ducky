import type { AgentEvent } from "../types/panel";
import { getBackgroundJobs, upsertBackgroundJob, dismissBackgroundJob, type BackgroundJob } from "./backgroundActivity";
import { subscribeAgentEvents, pushLocalAgentEvent } from "./useAgentEventBus";
import { getApi } from "./usePanelApi";
import { requestOpenChatTab } from "../navigation/openChatReference";
import { requestLatestChatActivity } from "../navigation/latestChatActivity";
import { requestToolActivity, toolActivityId } from "../navigation/toolActivity";
import { toolExitCode } from "../utils/toolExitCode";
import { unwrapCodingAgentTool } from "../utils/unwrapCodingAgentTool";

export function applyAgentBackgroundEvent(event: AgentEvent, title = event.title || event.conv_id || "Agent", now = Date.now()): void {
  const convId = event.conv_id;
  if (!convId) return;
  const turnId = `agent:${convId}`;
  const turn = getBackgroundJobs().find((j) => j.id === turnId);
  if (event.type === "agent_stopped" || event.type === "error") {
    for (const job of getBackgroundJobs()) {
      if (job.source !== "agent" || job.convId !== convId || job.phase !== "working") continue;
      if (event.run_id && job.runId && job.runId !== event.run_id) continue;
      upsertBackgroundJob({ id: job.id, title: job.toolId ? job.title : job.title.replace(/ is working$/, event.reason === "cancelled" ? " stopped" : " finished"), phase: event.type === "error" || event.reason === "error" ? "error" : "done",
        detail: event.reason === "cancelled" ? "Stopped" : job.toolId ? "Ended with agent turn" : "Finished",
        endedAt: now, ts: now });
    }
    return;
  }
  if (["agent_started", "text_delta", "thinking", "tool", "status"].includes(event.type)) {
    if (!turn || turn.phase !== "working" || (event.run_id && turn.runId !== event.run_id)) {
      upsertBackgroundJob({ id: turnId, source: "agent", title: `${title} is working`, detail: title,
        convId, runId: event.run_id, phase: "working", startedAt: now, endedAt: 0, ts: now });
    }
  }
  if ((event.type !== "tool" && event.type !== "tool_done") || !event.tool) return;
  const tool = event.tool;
  const toolId = toolActivityId(tool);
  const id = `tool:${convId}:${toolId}`;
  const prev = getBackgroundJobs().find((j) => j.id === id);
  if (prev?.runId && event.run_id && prev.runId !== event.run_id && event.type === "tool_done") return;
  const call = unwrapCodingAgentTool(tool.name, tool.arguments || {});
  const command = call.arguments.command ?? call.arguments.cmd ?? call.arguments.code ?? call.name;
  const code = toolExitCode(tool);
  const done = event.type === "tool_done";
  const failed = done && (event.success === false || tool.status === "error" || (code !== undefined && code !== 0));
  upsertBackgroundJob({ id, source: "agent", title: String(command).slice(0, 160),
    detail: `${title}${done ? ` · ${failed ? "Failed" : "Finished"}${code === undefined ? "" : ` · exit ${code}`}` : ""}`,
    convId, toolId, runId: event.run_id, terminalId: call.name === "ducky_terminal_run" ? String(call.arguments.session_id || "") : undefined,
    phase: done ? failed ? "error" : "done" : "working",
    startedAt: prev?.runId === event.run_id && prev?.startedAt ? prev.startedAt : tool.startedAt || now,
    endedAt: done ? now : 0, ts: now });
}

// A turn whose stop event never came (a group's side-chat note streams text with no turn
// around it; a chat deleted mid-run sends nothing) would read "is working" forever.
const SETTLE_MS = 10_000;

function endAgentJobs(convId: string, detail: string, now = Date.now()): void {
  for (const job of getBackgroundJobs()) {
    if (job.source !== "agent" || job.convId !== convId || job.phase !== "working") continue;
    upsertBackgroundJob({ id: job.id, phase: "done", detail: job.toolId ? "Ended with agent turn" : detail,
      title: job.toolId ? job.title : job.title.replace(/ is working$/, detail === "Stopped" ? " stopped" : " finished"),
      endedAt: now, ts: now });
  }
}

/** Close working rows the backend no longer runs (`running` null skips that check), and drop
 *  rows of chats that were deleted. */
export function reconcileAgentJobs(running: string[] | null, existing: Set<string> | null, now = Date.now()): void {
  for (const job of getBackgroundJobs()) {
    if (job.source !== "agent" || !job.convId) continue;
    if (existing && !existing.has(job.convId)) {
      dismissBackgroundJob(job.id);
      continue;
    }
    if (running && job.phase === "working" && !running.includes(job.convId) && now - job.ts > SETTLE_MS) {
      endAgentJobs(job.convId, "Finished", now);
    }
  }
}

/** Stop button in the tray: stops that chat's turn (a group stops its whole run). */
export async function stopAgentBackgroundJob(job: BackgroundJob): Promise<void> {
  if (!job.convId) return;
  const api = getApi();
  if (!api?.cancel_agent) throw new Error("Ducky is not connected");
  await api.cancel_agent(job.convId);
  endAgentJobs(job.convId, "Stopped");
}

/** Chat titles the tray last loaded (its "Waiting for you" rows name the chat). */
const chatTitles = new Map<string, string>();

export function chatTitleFor(convId: string): string {
  return chatTitles.get(convId) || "";
}

/** Mounted once by the header, independent of which chat panes are open. */
export function subscribeAgentBackgroundActivity(): () => void {
  const titles = new Map<string, string>();
  let existing: Set<string> | null = null;
  let checking = false;
  let active = true;
  const touched = new Set<string>();
  const api = getApi();
  let titleRefresh: Promise<void> | null = null;
  const refreshTitles = (): Promise<void> => {
    if (titleRefresh) return titleRefresh;
    titleRefresh = (async () => {
      const chats = await api?.list_all_conversations?.(true).catch(() => null);
      if (!active || !chats) return;
      existing = new Set(chats.map((chat) => chat.id));
      for (const chat of chats) {
        titles.set(chat.id, chat.title);
        chatTitles.set(chat.id, chat.title);
      }
      for (const job of getBackgroundJobs()) {
        const title = titles.get(job.convId || "");
        if (job.source === "agent" && title) {
          const detail = job.phase === "working" ? title : job.detail?.replace(job.convId!, title);
          upsertBackgroundJob({ id: job.id, title: job.toolId ? job.title : `${title}${job.phase === "working" ? " is working" : " finished"}`, detail, ts: job.ts });
        }
      }
    })().finally(() => { titleRefresh = null; });
    return titleRefresh;
  };
  const stop = subscribeAgentEvents((event) => {
    if (event.type === "chats_changed") {
      void refreshTitles().then(() => { if (active && existing) reconcileAgentJobs(null, existing); });
    } else if (event.conv_id && !titles.has(event.conv_id) && !touched.has(event.conv_id)) void refreshTitles();
    if (event.conv_id) touched.add(event.conv_id);
    applyAgentBackgroundEvent(event, titles.get(event.conv_id || "") || event.title || event.conv_id);
  });
  void (async () => {
    await refreshTitles();
    if (!active) return;
    // Recover turn visibility after reload; a chat need not be mounted.
    const ids = await api?.list_running_agents?.().catch(() => null);
    if (!active || !ids) return;
    for (const job of getBackgroundJobs()) {
      if (job.source === "agent" && job.phase === "working" && job.convId && !ids.includes(job.convId) && !touched.has(job.convId)) {
        upsertBackgroundJob({ id: job.id, phase: "done", detail: "No longer running", endedAt: Date.now() });
      }
    }
    for (const id of ids) {
      if (touched.has(id)) continue;
      applyAgentBackgroundEvent({ type: "agent_started", conv_id: id }, titles.get(id));
      const rows = await api?.load_messages?.(id).catch(() => []) || [];
      if (!active) return;
      if (touched.has(id)) continue;
      // History exposes only genuinely live pending calls as tool rows without a result.
      for (let i = 0; i < rows.length; i++) {
        const row = rows[i];
        if (row.role !== "tool" || !row.tool || rows[i + 1]?.role === "success" || rows[i + 1]?.role === "error") continue;
        applyAgentBackgroundEvent({ type: "tool", conv_id: id, run_id: row.run_id, tool: row.tool }, titles.get(id));
      }
    }
  })().catch(() => {});
  const timer = setInterval(() => {
    for (const job of getBackgroundJobs()) {
      if (job.source === "agent" && job.phase !== "working" && Date.now() - job.ts > 60_000) dismissBackgroundJob(job.id);
    }
    const stale = getBackgroundJobs().some((j) => j.source === "agent" && j.phase === "working" && Date.now() - j.ts > SETTLE_MS);
    if (!stale || checking) return;
    checking = true;
    void Promise.resolve(api?.list_running_agents?.())
      .then((ids) => { if (active && Array.isArray(ids)) reconcileAgentJobs(ids, existing); })
      .catch(() => {})
      .finally(() => { checking = false; });
  }, 5000);
  return () => { active = false; stop(); clearInterval(timer); };
}

export async function openAgentBackgroundJob(job: BackgroundJob): Promise<void> {
  if (job.terminalId) {
    const sessions = await getApi()?.terminal_list?.().catch(() => null);
    const raw = sessions?.sessions.find((s) => String(s.session_id) === job.terminalId);
    if (raw?.ws_url) {
      pushLocalAgentEvent({ type: "terminal_open", session_id: job.terminalId, ws_url: String(raw.ws_url),
        title: String(raw.title || job.title), shell: String(raw.shell || ""), cwd: String(raw.cwd || ""), activate: true });
      return;
    }
  }
  if (!job.convId) return;
  if (job.toolId) requestToolActivity(job.convId, job.toolId);
  else requestLatestChatActivity(job.convId);
  requestOpenChatTab(job.convId);
}
