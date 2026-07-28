/** Known UI language names for typo checks before translate. */

export const KNOWN_LANGUAGE_NAMES: readonly string[] = [
  "English",
  "Spanish",
  "French",
  "German",
  "Italian",
  "Portuguese",
  "Dutch",
  "Bulgarian",
  "Romanian",
  "Polish",
  "Czech",
  "Slovak",
  "Hungarian",
  "Greek",
  "Turkish",
  "Russian",
  "Ukrainian",
  "Belarusian",
  "Arabic",
  "Hebrew",
  "Persian",
  "Hindi",
  "Bengali",
  "Urdu",
  "Chinese",
  "Japanese",
  "Korean",
  "Vietnamese",
  "Thai",
  "Indonesian",
  "Malay",
  "Swedish",
  "Norwegian",
  "Danish",
  "Finnish",
  "Icelandic",
  "Catalan",
  "Basque",
  "Galician",
  "Croatian",
  "Serbian",
  "Bosnian",
  "Slovenian",
  "Macedonian",
  "Albanian",
  "Lithuanian",
  "Latvian",
  "Estonian",
  "Georgian",
  "Armenian",
  "Azerbaijani",
  "Kazakh",
  "Swahili",
  "Afrikaans",
  "Irish",
  "Welsh",
  "Scottish Gaelic",
  "Latin",
  "Esperanto",
];

const CODE_ALIASES: Record<string, string> = {
  en: "English",
  es: "Spanish",
  fr: "French",
  de: "German",
  it: "Italian",
  pt: "Portuguese",
  nl: "Dutch",
  bg: "Bulgarian",
  ro: "Romanian",
  pl: "Polish",
  cs: "Czech",
  sk: "Slovak",
  hu: "Hungarian",
  el: "Greek",
  tr: "Turkish",
  ru: "Russian",
  uk: "Ukrainian",
  ar: "Arabic",
  he: "Hebrew",
  fa: "Persian",
  hi: "Hindi",
  bn: "Bengali",
  ur: "Urdu",
  zh: "Chinese",
  ja: "Japanese",
  ko: "Korean",
  vi: "Vietnamese",
  th: "Thai",
  id: "Indonesian",
  ms: "Malay",
  sv: "Swedish",
  no: "Norwegian",
  da: "Danish",
  fi: "Finnish",
  ca: "Catalan",
  hr: "Croatian",
  sr: "Serbian",
  sl: "Slovenian",
  mk: "Macedonian",
  sq: "Albanian",
  lt: "Lithuanian",
  lv: "Latvian",
  et: "Estonian",
};

/** Native / alternate spellings → canonical English name. */
const NATIVE_ALIASES: Record<string, string> = {
  español: "Spanish",
  espanol: "Spanish",
  français: "French",
  francais: "French",
  deutsch: "German",
  italiano: "Italian",
  português: "Portuguese",
  portugues: "Portuguese",
  nederlands: "Dutch",
  български: "Bulgarian",
  bulgarisch: "Bulgarian",
  română: "Romanian",
  romana: "Romanian",
  polski: "Polish",
  čeština: "Czech",
  cestina: "Czech",
  magyar: "Hungarian",
  ελληνικά: "Greek",
  türkçe: "Turkish",
  turkce: "Turkish",
  русский: "Russian",
  українська: "Ukrainian",
  العربية: "Arabic",
  עברית: "Hebrew",
  हिंदी: "Hindi",
  中文: "Chinese",
  日本語: "Japanese",
  한국어: "Korean",
  "tiếng việt": "Vietnamese",
  ไทย: "Thai",
  "bahasa indonesia": "Indonesian",
  svenska: "Swedish",
  norsk: "Norwegian",
  dansk: "Danish",
  suomi: "Finnish",
};

function levenshtein(a: string, b: string): number {
  const m = a.length;
  const n = b.length;
  if (m === 0) return n;
  if (n === 0) return m;
  const prev = new Array<number>(n + 1);
  const cur = new Array<number>(n + 1);
  for (let j = 0; j <= n; j++) prev[j] = j;
  for (let i = 1; i <= m; i++) {
    cur[0] = i;
    for (let j = 1; j <= n; j++) {
      const cost = a[i - 1] === b[j - 1] ? 0 : 1;
      cur[j] = Math.min(cur[j - 1] + 1, prev[j] + 1, prev[j - 1] + cost);
    }
    for (let j = 0; j <= n; j++) prev[j] = cur[j];
  }
  return prev[n];
}

export type LanguageCheck =
  | { kind: "ok"; name: string }
  | { kind: "suggest"; input: string; suggestion: string }
  | { kind: "unknown"; input: string };

/** Exact / code / close typo match against known language names. */
export function checkLanguageName(raw: string): LanguageCheck {
  const input = String(raw || "").trim();
  if (!input) return { kind: "unknown", input: "" };

  const lower = input.toLowerCase();
  const byCode = CODE_ALIASES[lower];
  if (byCode) return { kind: "ok", name: byCode };

  const byNative = NATIVE_ALIASES[lower];
  if (byNative) return { kind: "ok", name: byNative };

  const exact = KNOWN_LANGUAGE_NAMES.find((n) => n.toLowerCase() === lower);
  if (exact) return { kind: "ok", name: exact };

  let bestName = "";
  let bestDist = Infinity;
  for (const name of KNOWN_LANGUAGE_NAMES) {
    const d = levenshtein(lower, name.toLowerCase());
    if (d < bestDist) {
      bestDist = d;
      bestName = name;
    }
  }

  // Typo budget: small absolute distance, and not rewriting most of the word.
  const maxDist = Math.max(2, Math.min(4, Math.floor(bestName.length * 0.35)));
  if (bestName && bestDist > 0 && bestDist <= maxDist) {
    return { kind: "suggest", input, suggestion: bestName };
  }

  return { kind: "unknown", input };
}
