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
};

const STORAGE_KEY = "uefn-background-activity";
const MAX_ITEMS = 40;
const JOB_EVENT = "ducky:background-job";
const ACTION_EVENT = "ducky:background-job-action";

type Snapshot = { jobs: BackgroundJob[] };

function readPersisted(): BackgroundJob[] {
  try {
    const raw = sessionStorage.getItem(STORAGE_KEY);
    if (!raw) return [];
    const parsed = JSON.parse(raw) as { jobs?: BackgroundJob[] };
    return Array.isArray(parsed?.jobs) ? parsed.jobs.slice(0, MAX_ITEMS) : [];
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

function emit(): void {
  snapshot = { jobs: snapshot.jobs };
  writePersisted(snapshot.jobs);
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

export function useBackgroundActivity(): Snapshot {
  return useSyncExternalStore(subscribe, getSnapshot, getServerSnapshot);
}

export function upsertBackgroundJob(patch: Partial<BackgroundJob> & { id: string }): BackgroundJob {
  const id = String(patch.id || "").trim();
  const now = Date.now();
  const prev = snapshot.jobs.find((j) => j.id === id);
  const next: BackgroundJob = {
    id,
    source: String(patch.source || prev?.source || "app"),
    title: String(patch.title || prev?.title || id),
    detail: patch.detail !== undefined ? String(patch.detail || "") : prev?.detail,
    percent: patch.percent !== undefined ? patch.percent : prev?.percent,
    phase: patch.phase || prev?.phase || "working",
    ts: typeof patch.ts === "number" ? patch.ts : now,
    cancelable: patch.cancelable !== undefined ? Boolean(patch.cancelable) : prev?.cancelable,
  };
  const rest = snapshot.jobs.filter((j) => j.id !== id);
  snapshot = { jobs: [next, ...rest].slice(0, MAX_ITEMS) };
  emit();
  return next;
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
}

/** ponytail: test-only peek. */
export function _peekBackgroundJobsForTests(): BackgroundJob[] {
  return snapshot.jobs;
}

/** ponytail: test-only reset. */
export function _resetBackgroundActivityForTests(): void {
  snapshot = { jobs: [] };
  try {
    sessionStorage.removeItem(STORAGE_KEY);
  } catch {
    /* ignore */
  }
  emit();
}
