import type { AgentEvent } from "../types/panel";
import { getBackgroundJobs, upsertBackgroundJob, dismissBackgroundJob, type BackgroundJob } from "./backgroundActivity";
import { subscribeAgentEvents } from "./useAgentEventBus";
import { getApi } from "./usePanelApi";
import { requestOpenChatTab } from "../navigation/openChatReference";
import { requestLatestChatActivity } from "../navigation/latestChatActivity";
import { agentStepText } from "../utils/agentActivity";
import { isArchiveFolderId } from "../utils/archiveFolder";

const TURN_EVENTS = new Set(["agent_started", "text_delta", "thinking", "tool", "tool_done", "status"]);

/** One tray row per running chat. Its detail line is what the turn is doing now; the
 *  tool calls themselves stay in the chat's own tool list, never in the tray. */
export function applyAgentBackgroundEvent(event: AgentEvent, title = event.title || event.conv_id || "Agent", now = Date.now()): void {
  const convId = event.conv_id;
  if (!convId) return;
  const turnId = `agent:${convId}`;
  const turn = getBackgroundJobs().find((j) => j.id === turnId);
  if (event.type === "run_expect") {
    // A waited sandbox/terminal run started (expected_ms > 0) or ended (0).
    if (turn?.phase === "working") {
      upsertBackgroundJob({ id: turnId, expectedMs: event.expected_ms || 0, estimated: Boolean(event.estimated),
        expectStartedAt: event.expected_ms ? now : 0, ts: now });
    }
    return;
  }
  if (event.type === "agent_stopped" || event.type === "error") {
    if (!turn || turn.phase !== "working") return;
    if (event.run_id && turn.runId && turn.runId !== event.run_id) return;
    const stopped = event.reason === "cancelled";
    const failed = event.type === "error" || event.reason === "error";
    upsertBackgroundJob({ id: turnId, title: turn.title.replace(/ is working$/, stopped ? " stopped" : " finished"),
      phase: failed ? "error" : "done", detail: stopped ? "Stopped" : failed ? "Ended with an error" : "Finished",
      endedAt: now, expectedMs: 0, ts: now });
    return;
  }
  if (!TURN_EVENTS.has(event.type)) return;
  const step = agentStepText(event) || "Working";
  const newRun = Boolean(event.run_id && turn?.runId && turn.runId !== event.run_id);
  if (!turn || turn.phase !== "working" || newRun) {
    upsertBackgroundJob({ id: turnId, source: "agent", title: `${title} is working`, detail: step,
      convId, runId: event.run_id, phase: "working", startedAt: now, endedAt: 0, expectedMs: 0, estimated: false, ts: now });
    return;
  }
  // Text streams in hundreds of deltas: only a new step touches the store.
  if (turn.detail !== step || (event.run_id && !turn.runId)) {
    upsertBackgroundJob({ id: turnId, detail: step, ...(event.run_id ? { runId: event.run_id } : {}), ts: now });
  }
}

// A turn whose stop event never came (a group's side-chat note streams text with no turn
// around it; a chat deleted mid-run sends nothing) would read "is working" forever.
const SETTLE_MS = 10_000;

function endAgentJobs(convId: string, detail: string, now = Date.now()): void {
  for (const job of getBackgroundJobs()) {
    if (job.source !== "agent" || job.convId !== convId || job.phase !== "working") continue;
    upsertBackgroundJob({ id: job.id, phase: "done", detail,
      title: job.title.replace(/ is working$/, detail === "Stopped" ? " stopped" : " finished"),
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

/** Chats the tray last loaded (its rows name the chat, with its group and an archived mark). */
type ChatInfo = { title: string; folder: string; parent: string; isGroup: boolean };
const chatInfo = new Map<string, ChatInfo>();
const titleListeners = new Set<() => void>();
let chatListLoad: Promise<string[] | null> | null = null;

/** Read every chat's title once (calls at the same time share it). Resolves to the chat ids, or null. */
export function loadChatTitles(): Promise<string[] | null> {
  if (chatListLoad) return chatListLoad;
  chatListLoad = (async () => {
    const chats = await getApi()?.list_all_conversations?.(true).catch(() => null);
    if (!chats) return null;
    for (const chat of chats) {
      chatInfo.set(chat.id, { title: chat.title, folder: chat.folder_id || "", parent: chat.parent_conv_id || "", isGroup: Boolean(chat.is_group) });
    }
    for (const cb of titleListeners) cb();
    return chats.map((chat) => chat.id);
  })().finally(() => { chatListLoad = null; });
  return chatListLoad;
}

export function subscribeChatTitles(cb: () => void): () => void {
  titleListeners.add(cb);
  return () => { titleListeners.delete(cb); };
}

export function chatTitleFor(convId: string): string {
  return chatInfo.get(convId)?.title || "";
}

/** A chat's name as the tray shows it: "Group · Chat", "(archived)" when the sidebar hides it. */
export function chatLabelFor(convId: string): string {
  const chat = chatInfo.get(convId);
  if (!chat?.title) return "";
  // A member sits in its group's folder; an archived member keeps parent_conv_id to its hub.
  let hub = chat.isGroup ? undefined : chatInfo.get(chat.parent);
  if (!hub?.isGroup && !chat.isGroup && chat.folder && !isArchiveFolderId(chat.folder)) {
    hub = [...chatInfo.values()].find((row) => row.isGroup && row.folder === chat.folder);
  }
  const name = hub?.isGroup && hub.title ? `${hub.title} · ${chat.title}` : chat.title;
  return isArchiveFolderId(chat.folder) || (hub?.isGroup && isArchiveFolderId(hub.folder)) ? `${name} (archived)` : name;
}

/** Mounted once by the header, independent of which chat panes are open. */
export function subscribeAgentBackgroundActivity(): () => void {
  const titles = new Map<string, string>();
  let existing: Set<string> | null = null;
  let checking = false;
  let active = true;
  const touched = new Set<string>();
  const api = getApi();
  // Rows from builds that listed every tool call (kept across reloads in sessionStorage).
  for (const job of getBackgroundJobs()) {
    if (job.source === "agent" && job.toolId) dismissBackgroundJob(job.id);
  }
  const refreshTitles = async (): Promise<void> => {
    const ids = await loadChatTitles();
    if (!active || !ids) return;
    existing = new Set(ids);
    for (const id of ids) titles.set(id, chatLabelFor(id));
    for (const job of getBackgroundJobs()) {
      const title = titles.get(job.convId || "");
      if (job.source === "agent" && title) {
        const verb = job.phase === "working" ? " is working" : / stopped$/.test(job.title) ? " stopped" : " finished";
        upsertBackgroundJob({ id: job.id, title: `${title}${verb}`, ts: job.ts });
      }
    }
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
      // Only the newest rows: this runs for every running agent at startup, over the bridge.
      const rows = await api?.load_messages?.(id, null, 40).catch(() => []) || [];
      if (!active) return;
      if (touched.has(id)) continue;
      // A tool row without a result is the call still running: that is the turn's step.
      for (let i = rows.length - 1; i >= 0; i--) {
        const row = rows[i];
        if (row.role !== "tool" || !row.tool || rows[i + 1]?.role === "success" || rows[i + 1]?.role === "error") continue;
        applyAgentBackgroundEvent({ type: "tool", conv_id: id, run_id: row.run_id, tool: row.tool }, titles.get(id));
        break;
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

/** Opens the chat, scrolled to its live activity, which flashes so it is easy to find. */
export async function openAgentBackgroundJob(job: BackgroundJob): Promise<void> {
  if (!job.convId) return;
  requestLatestChatActivity(job.convId);
  requestOpenChatTab(job.convId);
}
