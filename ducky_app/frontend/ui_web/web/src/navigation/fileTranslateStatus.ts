/**
 * Live status for visual file translate — hover cards show Translating… / Cached.
 */

export type FileTranslatePhase = "idle" | "translating" | "cached" | "error";

export type FileTranslateStatus = {
  phase: FileTranslatePhase;
  /** Short label for hover / banner. */
  label: string;
  updatedAt: number;
};

type Listener = () => void;

const statuses = new Map<string, FileTranslateStatus>();
const listeners = new Set<Listener>();

function normPath(path: string): string {
  return path.replace(/\\/g, "/").trim().toLowerCase();
}

function langSlug(lang: string): string {
  return lang.trim().toLowerCase().replace(/[^\w]+/g, "").slice(0, 12) || "lang";
}

export function fileTranslateStatusKey(path: string, lang: string): string {
  return `${normPath(path)}::${langSlug(lang)}`;
}

function emit(): void {
  for (const fn of [...listeners]) fn();
}

export function getFileTranslateStatus(path: string, lang: string): FileTranslateStatus {
  return (
    statuses.get(fileTranslateStatusKey(path, lang)) ?? {
      phase: "idle",
      label: "",
      updatedAt: 0,
    }
  );
}

export function setFileTranslateStatus(
  path: string,
  lang: string,
  phase: FileTranslatePhase,
  label = "",
): void {
  const key = fileTranslateStatusKey(path, lang);
  if (phase === "idle") {
    statuses.delete(key);
  } else {
    statuses.set(key, {
      phase,
      label: label || defaultLabel(phase),
      updatedAt: Date.now(),
    });
  }
  emit();
}

function defaultLabel(phase: FileTranslatePhase): string {
  if (phase === "translating") return "Translating…";
  if (phase === "cached") return "Cached";
  if (phase === "error") return "Failed";
  return "";
}

export function subscribeFileTranslateStatus(fn: Listener): () => void {
  listeners.add(fn);
  return () => {
    listeners.delete(fn);
  };
}

/** React-friendly snapshot for useSyncExternalStore. */
export function getFileTranslateStatusSnapshot(): number {
  let n = 0;
  for (const s of statuses.values()) n += s.updatedAt;
  return n + statuses.size;
}
