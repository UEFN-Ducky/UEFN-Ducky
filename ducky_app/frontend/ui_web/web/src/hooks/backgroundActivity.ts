import { useSyncExternalStore } from "react";

/** Header activity tray — live jobs + recent history. Plugins push via `ducky:background-job`. */

export type BackgroundJobPhase = "working" | "ready" | "done" | "error";

export type BackgroundJob = {
  id: string;
  source: string;
  title: string;
  detail?: string;
  percent?: number | null;
  phase: BackgroundJobPhase;
  ts: number;
  cancelable?: boolean;
  /** The label of the row's own button (Release); pressing it is heard through `onBackgroundJobAction`. */
  action?: string;
  convId?: string;
  toolId?: string;
  runId?: string;
  terminalId?: string;
  startedAt?: number;
  endedAt?: number;
  /** How long the running command should take (ms, 0 = unknown); `estimated`: from its past runs. */
  expectedMs?: number;
  estimated?: boolean;
  /** When that command started (the turn's `startedAt` is the whole turn). */
  expectStartedAt?: number;
};

const STORAGE_KEY = "uefn-background-activity";
const MAX_ITEMS = 40;
const JOB_EVENT = "ducky:background-job";
const ACTION_EVENT = "ducky:background-job-action";
/** Hundreds of agents upsert hundreds of times a frame; stringifying every job each time
 *  was the window's top long task (35 ms per frame at 300 agents). */
const PERSIST_DELAY_MS = 1000;

type Snapshot = { jobs: BackgroundJob[] };

function readPersisted(): BackgroundJob[] {
  try {
    const raw = sessionStorage.getItem(STORAGE_KEY);
    if (!raw) return [];
    const parsed = JSON.parse(raw) as { jobs?: BackgroundJob[] };
    const jobs = Array.isArray(parsed?.jobs) ? parsed.jobs : [];
    return jobs.filter((j) => j && j.phase && j.phase !== "ready").slice(0, MAX_ITEMS);
  } catch {
    return [];
  }
}

function writePersisted(jobs: BackgroundJob[]): void {
  try {
    sessionStorage.setItem(STORAGE_KEY, JSON.stringify({ jobs }));
  } catch {
    /* quota / private mode */
  }
}

let snapshot: Snapshot = { jobs: readPersisted() };
const listeners = new Set<() => void>();
let persistTimer: ReturnType<typeof setTimeout> | null = null;

function flushPersist(): void {
  if (persistTimer === null) return;
  clearTimeout(persistTimer);
  persistTimer = null;
  writePersisted(snapshot.jobs);
}

function emit(): void {
  snapshot = { jobs: snapshot.jobs };
  if (persistTimer === null) persistTimer = setTimeout(flushPersist, PERSIST_DELAY_MS);
  for (const fn of [...listeners]) fn();
}

function subscribe(fn: () => void): () => void {
  listeners.add(fn);
  return () => {
    listeners.delete(fn);
  };
}

function getSnapshot(): Snapshot {
  return snapshot;
}

function getServerSnapshot(): Snapshot {
  return { jobs: [] };
}

export function getBackgroundJobs(): BackgroundJob[] {
  return snapshot.jobs;
}

export function useBackgroundActivity(): Snapshot {
  return useSyncExternalStore(subscribe, getSnapshot, getServerSnapshot);
}

export function upsertBackgroundJob(patch: Partial<BackgroundJob> & { id: string }): BackgroundJob {
  const id = String(patch.id || "").trim();
  const now = Date.now();
  const prev = snapshot.jobs.find((j) => j.id === id);
  if ((patch.phase || "") === "ready") {
    if (prev) dismissBackgroundJob(id);
    return prev || {
      id,
      source: String(patch.source || "app"),
      title: String(patch.title || id),
      phase: "ready",
      ts: now,
    };
  }
  const next: BackgroundJob = {
    id,
    source: String(patch.source || prev?.source || "app"),
    title: String(patch.title || prev?.title || id),
    detail: patch.detail !== undefined ? String(patch.detail || "") : prev?.detail,
    percent: patch.percent !== undefined ? patch.percent : prev?.percent,
    phase: patch.phase || prev?.phase || "working",
    ts: typeof patch.ts === "number" ? patch.ts : now,
    convId: patch.convId ?? prev?.convId,
    toolId: patch.toolId ?? prev?.toolId,
    runId: patch.runId ?? prev?.runId,
    terminalId: patch.terminalId ?? prev?.terminalId,
    startedAt: patch.startedAt ?? prev?.startedAt,
    endedAt: patch.endedAt ?? prev?.endedAt,
    expectedMs: patch.expectedMs ?? prev?.expectedMs,
    estimated: patch.estimated ?? prev?.estimated,
    expectStartedAt: patch.expectStartedAt ?? prev?.expectStartedAt,
    cancelable: patch.cancelable !== undefined ? Boolean(patch.cancelable) : prev?.cancelable,
    action: patch.action !== undefined ? patch.action || undefined : prev?.action,
  };
  const rest = snapshot.jobs.filter((j) => j.id !== id);
  const all = [next, ...rest];
  snapshot = { jobs: [...all.filter((j) => j.phase === "working"), ...all.filter((j) => j.phase !== "working").slice(0, MAX_ITEMS)] };
  emit();
  if (prev?.phase === "working" && (next.phase === "done" || next.phase === "error")) {
    const seconds = Math.max(0, (now - (prev.startedAt || prev.ts)) / 1000);
    for (const fn of [...endedListeners]) {
      try {
        fn(next, seconds);
      } catch {
        // A listener never breaks the caller's update.
      }
    }
  }
  return next;
}

const endedListeners = new Set<(job: BackgroundJob, seconds: number) => void>();

/** A working job turned done or error in this window, after `seconds` of work. */
export function onBackgroundJobEnded(fn: (job: BackgroundJob, seconds: number) => void): () => void {
  endedListeners.add(fn);
  return () => {
    endedListeners.delete(fn);
  };
}

export function dismissBackgroundJob(id: string): void {
  const next = snapshot.jobs.filter((j) => j.id !== id);
  if (next.length === snapshot.jobs.length) return;
  snapshot = { jobs: next };
  emit();
}

export function clearFinishedBackgroundJobs(): void {
  const next = snapshot.jobs.filter((j) => j.phase === "working");
  if (next.length === snapshot.jobs.length) return;
  snapshot = { jobs: next };
  emit();
}

export function requestBackgroundJobCancel(id: string): void {
  if (!id) return;
  window.dispatchEvent(new CustomEvent(ACTION_EVENT, { detail: { id, action: "cancel" } }));
}

/** A row's own button (`job.action`) was pressed. */
export function requestBackgroundJobAction(id: string): void {
  if (!id) return;
  window.dispatchEvent(new CustomEvent(ACTION_EVENT, { detail: { id, action: "act" } }));
}

/** Hear a row's own button: `fn(id)`. Returns the unsubscribe. */
export function onBackgroundJobAction(fn: (id: string) => void): () => void {
  const listener = (ev: Event) => {
    const detail = (ev as CustomEvent<{ id?: string; action?: string }>).detail;
    if (detail?.action === "act" && detail.id) fn(detail.id);
  };
  window.addEventListener(ACTION_EVENT, listener);
  return () => window.removeEventListener(ACTION_EVENT, listener);
}

export function countWorkingBackgroundJobs(jobs: BackgroundJob[]): number {
  let n = 0;
  for (const job of jobs) {
    if (job.phase === "working") n += 1;
  }
  return n;
}

function onJobEvent(ev: Event): void {
  const detail = (ev as CustomEvent<Partial<BackgroundJob>>).detail;
  const id = String(detail?.id || "").trim();
  if (!id) return;
  upsertBackgroundJob({ ...detail, id });
}

if (typeof window !== "undefined") {
  window.addEventListener(JOB_EVENT, onJobEvent);
  // A reload inside the delay keeps the tray rows.
  window.addEventListener("pagehide", flushPersist);
  window.addEventListener("beforeunload", flushPersist);
}

/** ponytail: test-only, writes the pending save now. */
export function _flushBackgroundActivityPersistForTests(): void {
  flushPersist();
}

/** ponytail: test-only peek. */
export function _peekBackgroundJobsForTests(): BackgroundJob[] {
  return snapshot.jobs;
}

/** ponytail: test-only reset. */
export function _resetBackgroundActivityForTests(): void {
  snapshot = { jobs: [] };
  emit();
  if (persistTimer !== null) clearTimeout(persistTimer);
  persistTimer = null;
  try {
    sessionStorage.removeItem(STORAGE_KEY);
  } catch {
    /* ignore */
  }
}
