import { useSyncExternalStore } from "react";

import type { AgentEvent, AutomationRunDto, PanelPushEvent } from "../types/panel";
import { subscribeAgentEvents } from "./useAgentEventBus";
import { subscribePanelPush } from "./usePanelPushBus";
import { getApi } from "./usePanelApi";
import { getBackgroundJobs, upsertBackgroundJob } from "./backgroundActivity";
import { workflowIdFromJobId } from "./graphActivity";

/**
 * The workflow a chat's ducky is running, step by step, for the card above the plan.
 *
 * The runner's `workflow_run` start event names the calling chat, the workflow and its
 * happy-path steps; `workflow_step` events then say which step runs and how it ended.
 * Kept at module level so it follows the run while the chat's tab is not mounted.
 * The events can reach either UI bus, so applying one twice changes nothing.
 */

export type WorkflowStepState =
  "pending" | "running" | "ok" | "error" | "stopped";

export interface ChatWorkflowStep {
  node: string;
  label: string;
  type: string;
  state: WorkflowStepState;
  error?: string;
  /** Ran but is not on the happy path (a failure report, a fix step). */
  extra?: boolean;
  /** When it last started / finished (ms), for the time a long step has taken. */
  startedAt?: number;
  endedAt?: number;
}

export interface ChatWorkflowRun {
  workflowId: string;
  run: string;
  name: string;
  state: "running" | "done" | "error" | "stopped";
  error?: string;
  steps: ChatWorkflowStep[];
  /** Node id of the step running now ("" when none). */
  current: string;
  startedAt: number;
  endedAt?: number;
  /** Saved steps and output values for this execution. */
  result?: AutomationRunDto;
}

type WorkflowEvent = PanelPushEvent;

const byRun = new Map<string, ChatWorkflowRun>();
const clearedRuns = new Set<string>();
const eventListeners = new Set<(event: PanelPushEvent) => void>();
const latestEvents = new Map<string, PanelPushEvent>();
const snapshots = new Map<string, ChatWorkflowRun[]>();
const EMPTY: ChatWorkflowRun[] = [];
const runToChat = new Map<string, string>();
const listeners = new Set<() => void>();
let installed = false;

function emit(): void {
  snapshots.clear();
  for (const listener of listeners) listener();
}

function stepState(raw: string | undefined): WorkflowStepState {
  return raw === "ok" || raw === "error" || raw === "stopped" ? raw : "running";
}

export function applyWorkflowEvent(event: WorkflowEvent): void {
  const run = String(event.run || "");
  if (!run || clearedRuns.has(run)) return;
  const eventKey = [event.id, run, event.type, event.node, event.source, event.type === "workflow_run" ? event.state : ""].join(":");
  const previous = latestEvents.get(eventKey);
  const replayFinish = event.type === "workflow_run" && event.state !== "started" && byRun.get(run)?.state === "running";
  if (!replayFinish && previous?.workflow_sequence && event.workflow_sequence && event.workflow_sequence <= previous.workflow_sequence) return;
  if (!replayFinish && JSON.stringify(previous) === JSON.stringify(event)) return;
  latestEvents.set(eventKey, event);
  for (const listener of eventListeners) listener(event);
  if (event.type === "workflow_run" && event.state === "started") {
    const chat = String(event.conv || "");
    if (byRun.has(run)) return;
    runToChat.set(run, chat);
    byRun.set(run, {
      workflowId: String(event.id || ""),
      run,
      name: String(event.name || "Workflow"),
      state: "running",
      steps: (event.plan || []).map((p) => ({
        node: p.node,
        label: p.label || p.node,
        type: p.type || "",
        state: "pending",
      })),
      current: "",
      startedAt: Date.now(),
    });
    upsertBackgroundJob({ id: `graph-run:${event.id}:${run}`, source: "workflow", title: event.name || "Workflow", phase: "working", cancelable: true });
    emit();
    // The two push buses can deliver the finish before the start.
    const finished = [...latestEvents.values()].find((saved) =>
      saved.id === event.id && saved.run === run && saved.type === "workflow_run" && saved.state !== "started");
    if (finished) applyWorkflowEvent(finished);
    return;
  }
  const current = byRun.get(run);
  if (!current) return;

  if (event.type === "workflow_run") {
    if (current.state !== "running") return;
    const state =
      event.state === "stopped"
        ? "stopped"
        : event.state === "error"
          ? "error"
          : "done";
    const now = Date.now();
    const steps = current.steps.map((s) =>
      s.state === "running"
        ? ({
            ...s,
            state: state === "done" ? "ok" : state,
            endedAt: now,
          } as ChatWorkflowStep)
        : s,
    );
    byRun.set(run, {
      ...current,
      state,
      error: event.error || undefined,
      steps,
      current: "",
      endedAt: now,
    });
    upsertBackgroundJob({ id: `graph-run:${current.workflowId}:${run}`, source: "workflow", title: current.name, phase: state === "error" ? "error" : "done", detail: state === "stopped" ? "Stopped" : event.error || "Finished", cancelable: false });
    emit();
    return;
  }
  if (event.type !== "workflow_step" || current.state !== "running") return;
  const node = String(event.node || "");
  if (!node) return;
  const state = stepState(event.state);
  const index = current.steps.findIndex((s) => s.node === node);
  const prev = index >= 0 ? current.steps[index] : undefined;
  if (
    prev &&
    prev.state === state &&
    (prev.error || "") === (event.error || "")
  )
    return;
  const now = Date.now();
  const times =
    state === "running"
      ? { startedAt: now, endedAt: undefined }
      : { endedAt: now };
  const next: ChatWorkflowStep = prev
    ? { ...prev, state, error: event.error || undefined, ...times }
    : {
        node,
        label: event.label || node,
        type: "",
        state,
        error: event.error || undefined,
        extra: true,
        ...times,
      };
  const steps =
    index >= 0
      ? current.steps.map((s, i) => (i === index ? next : s))
      : [...current.steps, next];
  byRun.set(run, {
    ...current,
    steps,
    current:
      state === "running"
        ? node
        : current.current === node
          ? ""
          : current.current,
  });
  emit();
}

function onEvent(event: AgentEvent | PanelPushEvent): void {
  const type = String(event.type || "");
  if (type === "workflow_run" || type === "workflow_step" || type === "workflow_output")
    applyWorkflowEvent(event as WorkflowEvent);
}

let refreshPending: Promise<boolean> | undefined;

/** Reconcile saved activity with the authoritative process-shared run snapshot. */
export function refreshWorkflowRuns(): Promise<boolean> {
  if (refreshPending) return refreshPending;
  const pending = reconcileWorkflowRuns();
  refreshPending = pending;
  void pending.finally(() => { if (refreshPending === pending) refreshPending = undefined; });
  return pending;
}

async function reconcileWorkflowRuns(): Promise<boolean> {
  const jobsBefore = getBackgroundJobs().filter((job) => job.phase === "working" && workflowIdFromJobId(job.id));
  const runsBefore = [...byRun.values()].filter((run) => run.state === "running");
  try {
    const result = await getApi()?.workflow_run_snapshot?.();
    if (!result?.ok || !Array.isArray(result.events)) return false;
    const states = new Map<string, PanelPushEvent>();
    for (const event of result.events) {
      if (event.type === "workflow_run") states.set(`${event.id}:${event.run}`, event);
      onEvent(event);
    }
    for (const run of runsBefore) {
      if (byRun.get(run.run) !== run || states.has(`${run.workflowId}:${run.run}`)) continue;
      applyWorkflowEvent({ type: "workflow_run", id: run.workflowId, run: run.run, state: "stopped", error: "No longer running" });
    }
    for (const job of jobsBefore) {
      // A push during the request is newer than this snapshot.
      if (getBackgroundJobs().find((current) => current.id === job.id) !== job) continue;
      const wid = workflowIdFromJobId(job.id);
      const event = job.id.startsWith("graph-run:")
        ? states.get(job.id.slice("graph-run:".length))
        : [...states.values()].find((event) => event.id === wid && event.state === "started");
      if (event?.state === "started") continue;
      upsertBackgroundJob({
        id: job.id, phase: event?.state === "error" ? "error" : "done", cancelable: false,
        detail: event?.error || (event?.state === "stopped" ? "Stopped" : event ? "Finished" : "No longer running"),
      });
    }
    return true;
  } catch { return false; /* Keep live controls when the bridge is unavailable. */ }
}

/** A run that already ended is a successful reconciliation, not a Stop error. */
export async function stopWorkflowRun(workflowId: string, runId = ""): Promise<void> {
  const result = await getApi()?.stop_workflow?.(workflowId, runId);
  if (!result?.ok) throw new Error(result?.error || "Could not stop the workflow");
  const refreshed = await refreshWorkflowRuns();
  if (!result.stopped && !refreshed) throw new Error("Could not refresh the workflow status");
}

function install(): void {
  if (installed) return;
  installed = true;
  subscribePanelPush(onEvent);
  subscribeAgentEvents(onEvent);
  const hydrate = async () => {
    if (!listeners.size && !eventListeners.size) return;
    await refreshWorkflowRuns();
  };
  void hydrate();
  window.setInterval(() => void hydrate(), 1000);
}

function subscribe(listener: () => void): () => void {
  install();
  listeners.add(listener);
  return () => listeners.delete(listener);
}

/** Merge saved history without resetting another execution's live controls. */
export function hydrateWorkflowRunHistory(workflowId: string, name: string, results: AutomationRunDto[]): void {
  let changed = false;
  results.forEach((result, index) => {
    const run = result.run || `saved:${workflowId}:${result.started || 0}:${index}`;
    if (clearedRuns.has(run)) return;
    const previous = byRun.get(run);
    if (previous?.state === "running" || JSON.stringify(previous?.result) === JSON.stringify(result)) return;
    const state = previous?.state || (result.error === "Stopped" ? "stopped" : result.ok === false ? "error" : "done");
    byRun.set(run, {
      workflowId, run, name, state,
      error: result.error || previous?.error,
      steps: previous?.steps || (result.steps || []).map((step, i) => ({
        node: step.id || String(i), label: step.label || step.type || "Step", type: step.type || "",
        state: step.ok === false ? "error" : "ok", error: step.error,
      })),
      current: "",
      startedAt: result.started ? result.started * 1000 : previous?.startedAt || Date.now(),
      endedAt: result.ended ? result.ended * 1000 : previous?.endedAt,
      result,
    });
    if (!runToChat.has(run)) runToChat.set(run, result.conv_id || "");
    changed = true;
  });
  if (changed) emit();
}

/** Clear completed history while active runs and their Stop controls stay available. */
export function clearWorkflowRunHistory(workflowId: string, runIds?: ReadonlySet<string>): string[] {
  const removed: string[] = [];
  for (const [run, item] of byRun) {
    if (item.workflowId !== workflowId || item.state === "running" || (runIds && !runIds.has(run))) continue;
    clearedRuns.add(run);
    byRun.delete(run);
    runToChat.delete(run);
    removed.push(run);
  }
  for (const [key, event] of latestEvents) if (clearedRuns.has(String(event.run || ""))) latestEvents.delete(key);
  if (removed.length) emit();
  return removed;
}

/** Hide a finished run without removing another concurrent run's controls. */
export function dismissWorkflowRun(chatId: string, runId?: string): void {
  for (const [run, chat] of runToChat) {
    if (chat === chatId && (!runId || run === runId) && byRun.get(run)?.state !== "running") {
      byRun.delete(run);
      runToChat.delete(run);
    }
  }
  emit();
}

function snapshot(key: string, matches: (run: ChatWorkflowRun) => boolean): ChatWorkflowRun[] {
  let value = snapshots.get(key);
  if (!value) {
    value = [...byRun.values()].filter(matches).sort((a, b) => a.startedAt - b.startedAt);
    snapshots.set(key, value.length ? value : EMPTY);
  }
  return snapshots.get(key)!;
}

export function useChatWorkflowRuns(chatId: string): ChatWorkflowRun[] {
  return useSyncExternalStore(subscribe,
    () => snapshot("chat:" + chatId, (run) => runToChat.get(run.run) === chatId),
    () => EMPTY);
}

export function useWorkflowRuns(workflowId: string): ChatWorkflowRun[] {
  return useSyncExternalStore(subscribe,
    () => snapshot("workflow:" + workflowId, (run) => run.workflowId === workflowId),
    () => EMPTY);
}

export function useChatWorkflowRun(chatId: string): ChatWorkflowRun | null {
  const runs = useChatWorkflowRuns(chatId);
  return runs[runs.length - 1] ?? null;
}

/** Replay saved events when an editor opens, then follow every run independently. */
export function subscribeWorkflowEvents(listener: (event: PanelPushEvent) => void): () => void {
  install();
  eventListeners.add(listener);
  for (const event of latestEvents.values()) listener(event);
  return () => { eventListeners.delete(listener); };
}

export function resetWorkflowRunsForTests(): void {
  byRun.clear();
  clearedRuns.clear();
  runToChat.clear();
  latestEvents.clear();
  emit();
}
