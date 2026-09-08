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

/** One row of the level diff a bracketed opaque command produced. */
export interface OpaqueChange {
  change: string;
  label: string;
  path: string;
}

export interface OpaqueDetail {
  /** The script or console command as it was run, verbatim. */
  code: string;
  added: number;
  removed: number;
  moved: number;
  changes: OpaqueChange[];
  truncated: boolean;
}

/**
 * An opaque command's before-state, if this is one.
 *
 * `execute_python` and friends have no target and no inverse: what is recorded is
 * the code that ran and the difference a level snapshot noticed around it. That
 * is a different reading from "this property went from A to B", so it gets a
 * different view rather than being forced through the property table.
 */
export function opaqueDetail(before: Json): OpaqueDetail | null {
  const record = asRecord(before);
  if (!record) return null;
  const diff = asRecord(record.diff);
  const code = typeof record.code === "string" ? record.code : "";
  if (!diff && !code) return null;
  const rows = Array.isArray(diff?.changes) ? (diff.changes as Json[]) : [];
  return {
    code,
    added: Number(diff?.added ?? 0),
    removed: Number(diff?.removed ?? 0),
    moved: Number(diff?.moved ?? 0),
    changes: rows
      .map((row) => asRecord(row))
      .filter((row): row is Record<string, Json> => row !== null)
      .map((row) => ({
        change: String(row.change ?? ""),
        label: String(row.label ?? ""),
        path: String(row.path ?? row.id ?? ""),
      })),
    truncated: Boolean(diff?.truncated),
  };
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
  const opaque = useMemo(() => opaqueDetail(parsedBefore), [parsedBefore]);
  const rows = useMemo(() => diffFields(parsedBefore, parsedAfter), [parsedBefore, parsedAfter]);
  const changed = rows.filter((r) => r.changed);

  if (opaque) {
    return (
      <div className="json-diff">
        {summary ? <p className="json-diff-summary">{summary}</p> : null}
        {opaque.code ? (
          <section className="json-diff-code">
            <h4>What ran</h4>
            <pre>{opaque.code}</pre>
          </section>
        ) : null}
        <section>
          <h4>What changed in the level</h4>
          {opaque.changes.length === 0 ? (
            <p className="json-diff-note">
              Nothing the level snapshot could see. It watches actors, so a change to
              something else would not appear here.
            </p>
          ) : (
            <>
              <p className="json-diff-summary">
                {[
                  opaque.added ? `${opaque.added} added` : "",
                  opaque.removed ? `${opaque.removed} removed` : "",
                  opaque.moved ? `${opaque.moved} moved` : "",
                ]
                  .filter(Boolean)
                  .join(", ")}
              </p>
              <ul className="json-diff-changes">
                {opaque.changes.map((row) => (
                  <li key={`${row.change}:${row.path}`}>
                    <span className={`json-diff-change json-diff-change--${row.change}`}>
                      {row.change}
                    </span>
                    <span className="json-diff-change-label">{row.label || row.path}</span>
                  </li>
                ))}
              </ul>
              {opaque.truncated ? (
                <p className="json-diff-note">Only the first {opaque.changes.length} are listed.</p>
              ) : null}
            </>
          )}
        </section>
      </div>
    );
  }

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
