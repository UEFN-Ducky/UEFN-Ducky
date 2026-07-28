import type { RichBlock } from "../../types/richContent";

const MAX_CELL = 120;
const MAX_ROWS = 50;
/** Side-by-side key/value only when every value is short. */
const MAX_FLAT_VALUE = 80;

function stringifyValue(value: unknown): string {
  if (value === null || value === undefined) return "";
  if (typeof value === "string") return value;
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  try {
    return JSON.stringify(value, null, 2);
  } catch {
    return String(value);
  }
}

function truncateCell(text: string): string {
  if (text.length <= MAX_CELL) return text;
  return `${text.slice(0, MAX_CELL - 1)}…`;
}

function tryParseJsonString(value: unknown): unknown {
  if (typeof value !== "string") return value;
  const t = value.trim();
  if (!t || (t[0] !== "{" && t[0] !== "[")) return value;
  try {
    return JSON.parse(t);
  } catch {
    return value;
  }
}

/** MCP / panel wrappers often nest the real payload under `result` or `data`. */
function unwrapEnvelope(value: unknown): unknown {
  let cur = tryParseJsonString(value);
  for (let i = 0; i < 3; i++) {
    if (!cur || typeof cur !== "object" || Array.isArray(cur)) break;
    const obj = cur as Record<string, unknown>;
    const keys = Object.keys(obj);
    // { result: <payload> } or { result, ok?, tool?, error? }
    if ("result" in obj) {
      const meta = keys.filter((k) => !["result", "ok", "tool", "error", "hint"].includes(k));
      if (meta.length === 0) {
        cur = tryParseJsonString(obj.result);
        continue;
      }
    }
    // { data: <payload>, ok?, tool?, … } — peel when data is the only substance
    if ("data" in obj && obj.data !== undefined && obj.data !== null) {
      const meta = keys.filter((k) => !["data", "ok", "tool", "error", "hint"].includes(k));
      if (meta.length === 0) {
        cur = tryParseJsonString(obj.data);
        continue;
      }
    }
    break;
  }
  return cur;
}

function uniformObjectRows(items: Record<string, unknown>[]): { headers: string[]; rows: string[][] } | null {
  if (items.length === 0) return null;
  const keys = Object.keys(items[0] ?? {});
  if (keys.length === 0) return null;
  const allSame = items.every((row) => {
    const rowKeys = Object.keys(row);
    return rowKeys.length === keys.length && keys.every((k) => k in row);
  });
  if (!allSame) return null;
  const headers = keys.slice(0, 8);
  const rows = items.slice(0, MAX_ROWS).map((row) =>
    headers.map((h) => truncateCell(stringifyValue(row[h]))),
  );
  return { headers, rows };
}

function isShortScalar(value: unknown): boolean {
  if (value === null || value === undefined) return true;
  if (typeof value === "number" || typeof value === "boolean") return true;
  if (typeof value === "string") return value.length <= MAX_FLAT_VALUE && !value.includes("\n");
  return false;
}

function flatKeyValue(obj: Record<string, unknown>): RichBlock | null {
  const entries = Object.entries(obj).filter(([, v]) => isShortScalar(v));
  if (entries.length === 0) return null;
  // Don't use KV when most of the object is nested/long — show JSON instead.
  if (entries.length < Object.keys(obj).length) return null;
  return {
    type: "key_value",
    pairs: entries.map(([key, value]) => ({ key, value: stringifyValue(value) })),
  };
}

function asJsonCode(value: unknown): RichBlock {
  return { type: "code", language: "json", text: stringifyValue(value) };
}

function formatDataValue(data: unknown): RichBlock[] {
  const unwrapped = unwrapEnvelope(data);
  if (unwrapped === null || unwrapped === undefined) return [];
  if (typeof unwrapped === "string") {
    const trimmed = unwrapped.trim();
    if (!trimmed) return [];
    return [{ type: "code", text: unwrapped }];
  }
  if (Array.isArray(unwrapped)) {
    if (unwrapped.length === 0) return [{ type: "paragraph", text: "(empty list)" }];
    if (unwrapped.every((x) => typeof x === "string")) {
      return [{ type: "list", items: unwrapped as string[] }];
    }
    if (unwrapped.every((x) => x && typeof x === "object" && !Array.isArray(x))) {
      const table = uniformObjectRows(unwrapped as Record<string, unknown>[]);
      if (table) return [{ type: "table", ...table }];
    }
    return [asJsonCode(unwrapped)];
  }
  if (typeof unwrapped === "object") {
    const obj = unwrapped as Record<string, unknown>;
    const listKeys = ["actors", "devices", "items", "assets", "results", "entries", "errors", "files"];
    for (const key of listKeys) {
      const arr = obj[key];
      if (Array.isArray(arr) && arr.length > 0 && arr.every((x) => x && typeof x === "object")) {
        const table = uniformObjectRows(arr as Record<string, unknown>[]);
        if (table) {
          const rest = { ...obj };
          delete rest[key];
          const blocks: RichBlock[] = [{ type: "table", headers: table.headers, rows: table.rows }];
          const restFlat = flatKeyValue(rest);
          if (restFlat) blocks.push(restFlat);
          else if (Object.keys(rest).length > 0) blocks.push(asJsonCode(rest));
          return blocks;
        }
      }
    }
    const flat = flatKeyValue(obj);
    if (flat) return [flat];
    // Complex object → same full-width JSON as Arguments (not a sideways "result" column).
    return [asJsonCode(obj)];
  }
  return [{ type: "paragraph", text: stringifyValue(unwrapped) }];
}

function parseToolPayload(raw: string): unknown {
  const trimmed = raw.trim();
  if (!trimmed) return null;
  try {
    return JSON.parse(trimmed);
  } catch {
    return trimmed;
  }
}

/** Convert tool result JSON string into RichBlock[] for display. */
export function autoFormatToolJson(raw: string): RichBlock[] {
  const parsed = unwrapEnvelope(parseToolPayload(raw));
  if (parsed === null) return [];

  if (typeof parsed === "string") {
    return [{ type: "code", text: parsed }];
  }

  if (typeof parsed === "object" && !Array.isArray(parsed)) {
    const obj = parsed as Record<string, unknown>;
    const blocks: RichBlock[] = [];

    if (obj.ok === false) {
      const err = stringifyValue(obj.error ?? obj.message ?? "Tool failed");
      blocks.push({ type: "callout", tone: "error", text: err });
      if (obj.hint) blocks.push({ type: "callout", tone: "info", text: stringifyValue(obj.hint) });
      const payload = obj.data !== undefined ? obj.data : obj.result !== undefined ? obj.result : null;
      if (payload !== null && payload !== undefined) blocks.push(...formatDataValue(payload));
      else if (blocks.length === 1) {
        // Show the rest of the error object if useful.
        const rest = { ...obj };
        delete rest.ok;
        delete rest.error;
        delete rest.message;
        delete rest.hint;
        if (Object.keys(rest).length) blocks.push(...formatDataValue(rest));
      }
      return blocks;
    }

    if (obj.ok === true || "data" in obj || "result" in obj || "error" in obj) {
      if (obj.hint) blocks.push({ type: "callout", tone: "info", text: stringifyValue(obj.hint) });
      if (obj.data !== undefined) blocks.push(...formatDataValue(obj.data));
      else if (obj.result !== undefined) blocks.push(...formatDataValue(obj.result));
      else if (obj.error) blocks.push({ type: "callout", tone: "error", text: stringifyValue(obj.error) });
      if (blocks.length > 0) return blocks;
    }

    return formatDataValue(obj);
  }

  return formatDataValue(parsed);
}
