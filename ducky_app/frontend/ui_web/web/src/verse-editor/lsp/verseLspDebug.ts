export type VerseLspLogLevel = "info" | "warn" | "error";

export type VerseLspLogEntry = {
  ts: string;
  level: VerseLspLogLevel;
  category: string;
  event: string;
  detail?: unknown;
};

const LOG_PREFIX = "[verse-lsp]";
const RING_MAX = 400;
const ring: VerseLspLogEntry[] = [];
let hooksInstalled = false;

function isVerbose(): boolean {
  try {
    return localStorage.getItem("verse-lsp-verbose") === "1";
  } catch {
    return false;
  }
}

function pushEntry(level: VerseLspLogLevel, category: string, event: string, detail?: unknown): void {
  const entry: VerseLspLogEntry = {
    ts: new Date().toISOString(),
    level,
    category,
    event,
    detail,
  };
  ring.push(entry);
  if (ring.length > RING_MAX) ring.shift();

  const line = `${LOG_PREFIX} [${category}] ${event}`;
  if (level === "error") {
    if (detail !== undefined) console.error(line, detail);
    else console.error(line);
  } else if (level === "warn") {
    if (detail !== undefined) console.warn(line, detail);
    else console.warn(line);
  } else if (isVerbose()) {
    if (detail !== undefined) console.log(line, detail);
    else console.log(line);
  }
}

export function verseLspLog(category: string, event: string, detail?: unknown): void {
  installVerseLspDebugHooks();
  pushEntry("info", category, event, detail);
}

export function verseLspWarn(category: string, event: string, detail?: unknown): void {
  installVerseLspDebugHooks();
  pushEntry("warn", category, event, detail);
}

export function verseLspError(category: string, event: string, detail?: unknown): void {
  installVerseLspDebugHooks();
  pushEntry("error", category, event, detail);
}

export function verseLspLogError(category: string, event: string, err: unknown): void {
  const detail =
    err instanceof Error
      ? { message: err.message, stack: err.stack, name: err.name }
      : err;
  verseLspError(category, event, detail);
}

export function getVerseLspLog(): VerseLspLogEntry[] {
  return [...ring];
}

export function clearVerseLspLog(): void {
  ring.length = 0;
}

export function installVerseLspDebugHooks(): void {
  if (hooksInstalled || typeof window === "undefined") return;
  hooksInstalled = true;

  window.addEventListener("error", (ev) => {
    verseLspError("window", "uncaught error", {
      message: ev.message,
      filename: ev.filename,
      lineno: ev.lineno,
      colno: ev.colno,
      error: ev.error instanceof Error ? ev.error.message : String(ev.error),
    });
  });

  window.addEventListener("unhandledrejection", (ev) => {
    const reason = ev.reason;
    const msg = reason instanceof Error ? reason.message : String(reason ?? "");
    if (msg === "Canceled" || msg.endsWith(": Canceled")) {
      ev.preventDefault();
      return;
    }
    verseLspError("window", "unhandled promise rejection", {
      reason: reason instanceof Error ? reason.message : reason,
    });
  });

  const w = window as unknown as {
    __verseLspLog?: () => VerseLspLogEntry[];
    __verseLspLogClear?: () => void;
    __verseLspDiagnostics?: () => unknown;
  };
  w.__verseLspLog = getVerseLspLog;
  w.__verseLspLogClear = clearVerseLspLog;
}
