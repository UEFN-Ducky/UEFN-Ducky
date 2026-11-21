/** Language list helpers for the Translation plugin (user-added only). */

export function parseCustomLanguages(raw: unknown): string[] {
  if (typeof raw !== "string" || !raw.trim()) return [];
  return raw
    .split(",")
    .map((s) => s.trim())
    .filter(Boolean)
    .filter((code, i, arr) => arr.findIndex((c) => c.toLowerCase() === code.toLowerCase()) === i);
}

export function serializeCustomLanguages(codes: string[]): string {
  return codes.map((c) => c.trim()).filter(Boolean).join(",");
}

/** Accept a free-form name ("Spanish") or code ("es") as the language id for the LLM. */
export function normalizeLanguageCode(input: string): string {
  return input.trim().replace(/\s+/g, " ").slice(0, 48);
}

export function isEnglishLang(lang: string): boolean {
  const c = lang.trim().toLowerCase();
  return !c || c === "en" || c === "eng" || c === "english";
}
