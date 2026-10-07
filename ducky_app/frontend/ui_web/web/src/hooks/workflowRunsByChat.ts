import { useSyncExternalStore } from "react";

import type { AgentEvent, PanelPushEvent } from "../types/panel";
import { subscribeAgentEvents } from "./useAgentEventBus";
import { subscribePanelPush } from "./usePanelPushBus";
import { getApi } from "./usePanelApi";
import { upsertBackgroundJob } from "./backgroundActivity";

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
}

type WorkflowEvent = PanelPushEvent;

const byRun = new Map<string, ChatWorkflowRun>();
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
  if (!run) return;
  const eventKey = [event.id, run, event.type, event.node, event.source, event.type === "workflow_run" ? event.state : ""].join(":");
  const previous = latestEvents.get(eventKey);
  if (previous?.workflow_sequence && event.workflow_sequence && event.workflow_sequence <= previous.workflow_sequence) return;
  if (JSON.stringify(previous) === JSON.stringify(event)) return;
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

function install(): void {
  if (installed) return;
  installed = true;
  subscribePanelPush(onEvent);
  subscribeAgentEvents(onEvent);
  const hydrate = async () => {
    if (!listeners.size && !eventListeners.size) return;
    try {
      const result = await getApi()?.workflow_run_snapshot?.();
      for (const event of result?.events || []) onEvent(event);
    } catch { /* A disconnected bridge retries at the next poll. */ }
  };
  void hydrate();
  window.setInterval(() => void hydrate(), 1000);
}

function subscribe(listener: () => void): () => void {
  install();
  listeners.add(listener);
  return () => listeners.delete(listener);
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
    value = [...byRun.values()].filter(matches);
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
  runToChat.clear();
  latestEvents.clear();
  emit();
}
