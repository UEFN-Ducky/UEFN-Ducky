import { useMemo } from "react";

/**
 * Before/after for an editor change.
 *
 * Editor entries store JSON, not text, so the line differ in `ToolFileEditDiff`
 * has nothing useful to say about them. This lists the properties that actually
 * changed — the reading a user wants for "what did moving that actor do?" — and
 * falls back to the raw text when the blob is not JSON.
 */

export interface JsonDiffViewProps {
  before: string;
  after: string;
  /** Shown above the table, e.g. "moved +250 on Z". */
  summary?: string;
}

type Json = unknown;

interface FieldRow {
  key: string;
  before: string;
  after: string;
  changed: boolean;
}

function parse(text: string): Json | undefined {
  const raw = (text || "").trim();
  if (!raw) return undefined;
  try {
    return JSON.parse(raw) as Json;
  } catch {
    return undefined;
  }
}

/** Values are formatted, never truncated: a half-shown coordinate is worse than a long one. */
export function formatValue(value: Json): string {
  if (value === undefined) return "—";
  if (value === null) return "null";
  if (typeof value === "string") return value;
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  if (Array.isArray(value)) return `[${value.map((v) => formatValue(v)).join(", ")}]`;
  try {
    return JSON.stringify(value);
  } catch {
    return String(value);
  }
}

function asRecord(value: Json): Record<string, Json> | null {
  return value !== null && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, Json>)
    : null;
}

export function diffFields(before: Json, after: Json): FieldRow[] {
  const b = asRecord(before);
  const a = asRecord(after);
  if (!b && !a) return [];
  const keys = [...new Set([...Object.keys(b ?? {}), ...Object.keys(a ?? {})])];
  return keys.map((key) => {
    const beforeText = formatValue(b?.[key]);
    const afterText = formatValue(a?.[key]);
    return { key, before: beforeText, after: afterText, changed: beforeText !== afterText };
  });
}

export function JsonDiffView({ before, after, summary }: JsonDiffViewProps) {
  const parsedBefore = useMemo(() => parse(before), [before]);
  const parsedAfter = useMemo(() => parse(after), [after]);
  const rows = useMemo(() => diffFields(parsedBefore, parsedAfter), [parsedBefore, parsedAfter]);
  const changed = rows.filter((r) => r.changed);

  return (
    <div className="json-diff">
      {summary ? <p className="json-diff-summary">{summary}</p> : null}
      {rows.length > 0 ? (
        <table className="json-diff-table">
          <thead>
            <tr>
              <th>Property</th>
              <th>Before</th>
              <th>After</th>
            </tr>
          </thead>
          <tbody>
            {(changed.length > 0 ? changed : rows).map((row) => (
              <tr key={row.key} className={row.changed ? "json-diff-row--changed" : ""}>
                <td className="json-diff-key">{row.key}</td>
                <td className="json-diff-before">{row.before}</td>
                <td className="json-diff-after">{row.after}</td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : (
        <div className="json-diff-raw">
          <section>
            <h4>Before</h4>
            <pre>{before || "—"}</pre>
          </section>
          <section>
            <h4>After</h4>
            <pre>{after || "—"}</pre>
          </section>
        </div>
      )}
      {rows.length > 0 && changed.length === 0 ? (
        <p className="json-diff-note">Nothing changed between the recorded states.</p>
      ) : null}
    </div>
  );
}
