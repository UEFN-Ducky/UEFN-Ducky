/** Console logging for Verse editor (grammar, snippets, setup). LSP uses verseLspDebug. */

const PREFIX = "[verse-editor]";

export function verseEditorLog(category: string, event: string, detail?: unknown): void {
  const line = `${PREFIX} [${category}] ${event}`;
  if (detail !== undefined) console.log(line, detail);
  else console.log(line);
}

export function verseEditorWarn(category: string, event: string, detail?: unknown): void {
  const line = `${PREFIX} [${category}] ${event}`;
  if (detail !== undefined) console.warn(line, detail);
  else console.warn(line);
}

export function verseEditorError(category: string, event: string, detail?: unknown): void {
  const line = `${PREFIX} [${category}] ${event}`;
  if (detail !== undefined) console.error(line, detail);
  else console.error(line);
}

export function verseEditorLogError(category: string, event: string, err: unknown): void {
  const detail =
    err instanceof Error
      ? { message: err.message, stack: err.stack, name: err.name }
      : err;
  verseEditorError(category, event, detail);
}

if (typeof window !== "undefined") {
  const w = window as unknown as { __verseEditorLogInstalled?: boolean };
  if (!w.__verseEditorLogInstalled) {
    w.__verseEditorLogInstalled = true;
    verseEditorLog("boot", "console helpers active — filter DevTools by [verse-editor]");
  }
}
