/** Open a read-only visual translation of a Verse file (Translation plugin). */

let openHandler: ((relativePath: string) => void) | null = null;

export function registerOpenVerseTranslatedTab(fn: (relativePath: string) => void): () => void {
  openHandler = fn;
  return () => {
    if (openHandler === fn) openHandler = null;
  };
}

export function openVerseTranslatedTab(relativePath: string): void {
  const path = String(relativePath || "").trim();
  if (!path || !openHandler) return;
  openHandler(path);
}

export function verseTranslatedTabId(relativePath: string, lang: string): string {
  const p = relativePath.replace(/\\/g, "/").toLowerCase();
  const l = lang.trim().toLowerCase() || "lang";
  return `verse-translated:${l}:${p}`;
}

export function readTranslationUiLang(): string {
  try {
    const raw = localStorage.getItem("uefn-plugin-ui-prefs");
    const all = raw ? (JSON.parse(raw) as Record<string, Record<string, unknown>>) : {};
    const lang = all.translation?.language;
    return typeof lang === "string" ? lang.trim() : "en";
  } catch {
    return "en";
  }
}

export function readTranslationModel(): string {
  try {
    const raw = localStorage.getItem("uefn-plugin-ui-prefs");
    const all = raw ? (JSON.parse(raw) as Record<string, Record<string, unknown>>) : {};
    const model = all.translation?.model;
    return typeof model === "string" ? model.trim() : "";
  } catch {
    return "";
  }
}
