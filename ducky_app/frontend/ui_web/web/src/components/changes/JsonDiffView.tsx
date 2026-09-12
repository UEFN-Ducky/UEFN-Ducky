import { useMemo, type ReactNode } from "react";

import { renderHighlightedJson } from "../tool-cards/highlightJson";
import { renderHighlightedPython } from "../tool-cards/highlightPython";
import { readableLabel, recordedResult } from "./ledgerPresentation";

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

/** Pretty-print a compact JSON string so the modal can show it like an editor. */
export function prettyCode(text: string): string {
  const raw = (text || "").trim();
  if (!raw || raw === "—" || raw === "null") return text;
  try {
    return JSON.stringify(JSON.parse(raw), null, 2);
  } catch {
    return text;
  }
}

export function isStructuredText(text: string): boolean {
  const raw = (text || "").trim();
  if (raw.startsWith("{") && raw.endsWith("}")) {
    try {
      JSON.parse(raw);
      return true;
    } catch {
      return false;
    }
  }
  // Short arrays (location/rotation) stay on one line; object lists get a code block.
  if (raw.startsWith("[") && raw.endsWith("]") && raw.length > 60) {
    try {
      JSON.parse(raw);
      return true;
    } catch {
      return false;
    }
  }
  return raw.includes("\n") && raw.length > 80;
}

function CodePane({ code, lang }: { code: string; lang: "python" | "json" }) {
  return (
    <pre className="tool-execution-card-args-pre tool-execution-card-args-pre--python json-diff-code-pre">
      <code className="tool-py-code">
        {lang === "python" ? renderHighlightedPython(code) : renderHighlightedJson(code)}
      </code>
    </pre>
  );
}

function ValueView({ text }: { text: string }): ReactNode {
  if (isStructuredText(text) || text.length > 240) {
    return <details className="ledger-technical"><summary>View recorded value</summary><CodePane code={prettyCode(text)} lang="json" /></details>;
  }
  return text;
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

/** After-blob the journal writes for an editor op: tool args, not the new world state. */
const ENVELOPE_KEYS = new Set(["params", "created", "after"]);
const NOISE_KEYS = new Set([
  "actor_path",
  "actor_paths",
  "params",
  "created",
  "after",
  "command",
  "replace",
  "ok",
  "error",
  "field",
]);

const FIELD_LABELS: Record<string, string> = {
  label: "Name",
  folder: "Folder",
  tags: "Tags",
  location: "Location",
  rotation: "Rotation",
  scale: "Scale",
  parent: "Attached to",
};

function emptyish(value: Json): boolean {
  if (value == null || value === "") return true;
  return Array.isArray(value) && value.length === 0;
}

function listText(value: Json): string {
  if (emptyish(value)) return "—";
  if (Array.isArray(value)) return value.map((item) => (typeof item === "string" ? item : formatValue(item))).join(", ");
  return formatValue(value);
}

function createdLabels(value: Json): string {
  if (!Array.isArray(value) || value.length === 0) return "";
  return value
    .map((item) => {
      const rec = asRecord(item);
      return rec ? String(rec.label || rec.path || rec.id || "") : formatValue(item);
    })
    .filter(Boolean)
    .join(", ");
}

/** True when `after` is the journal envelope `{params, after, created}`, not a property snapshot. */
export function isEditorEnvelope(after: Json): boolean {
  const rec = asRecord(after);
  return Boolean(rec && asRecord(rec.params) && [...ENVELOPE_KEYS].some((key) => key in rec));
}

function editorAfterState(after: Json): Record<string, Json> {
  const rec = asRecord(after);
  if (!rec) return {};
  const params = asRecord(rec.params) ?? {};
  const inner = asRecord(rec.after) ?? {};
  return { ...params, ...inner };
}

function linkValue(record: Record<string, Json>): Json {
  if (!emptyish(record.target_paths)) return record.target_paths;
  if (!emptyish(record.target_path)) return record.target_path;
  if (!emptyish(record.asset_paths)) return record.asset_paths;
  return record.value;
}

/**
 * Before is a world snapshot; after is often `{params, after, created}`.
 * Diff those as the one or two properties a person cares about — not every key.
 */
export function projectEditorDiff(before: Json, after: Json): FieldRow[] {
  const b = asRecord(before) ?? {};
  const a = isEditorEnvelope(after) ? editorAfterState(after) : (asRecord(after) ?? {});
  const rows: FieldRow[] = [];

  const field = String(a.field ?? b.field ?? "");
  if (field && ("field" in b || "field" in a || "target_paths" in b || "target_paths" in a)) {
    const beforeText = listText(linkValue(b));
    const afterText = listText(linkValue(a));
    if (beforeText !== afterText) {
      rows.push({ key: field, before: beforeText, after: afterText, changed: true });
    }
  }

  const bProps = asRecord(b.properties) ?? {};
  const aProps = asRecord(a.properties) ?? {};
  const keys = new Set([...Object.keys(b), ...Object.keys(a), ...Object.keys(bProps), ...Object.keys(aProps)]);
  for (const key of keys) {
    if (NOISE_KEYS.has(key) || key === "properties" || key === "target_paths" || key === "target_path" || key === "asset_paths" || key === "value") {
      continue;
    }
    const inAfter = key in a || key in aProps;
    const beforeVal = key in bProps ? bProps[key] : b[key];
    const afterVal = key in aProps ? aProps[key] : a[key];
    // Envelope after often omits a property we captured before — that is not a clear.
    if (isEditorEnvelope(after) && !inAfter) continue;
    const beforeText = listText(beforeVal);
    const afterText = listText(afterVal);
    if (beforeText === afterText) continue;
    rows.push({ key: FIELD_LABELS[key] || key, before: beforeText, after: afterText, changed: true });
  }

  const made = createdLabels(asRecord(after)?.created);
  if (made) rows.push({ key: "Created", before: "—", after: made, changed: true });
  return rows;
}

export function editorStory(rows: FieldRow[], fallback = ""): string {
  if (rows.length === 1) {
    const row = rows[0];
    if (isStructuredText(row.before) || isStructuredText(row.after) || row.before.length + row.after.length > 240) return `Updated ${readableLabel(row.key)}`;
    if (row.key === "Name") return `Renamed ${row.before} to ${row.after}`;
    if (row.key === "Created") return `Created ${row.after}`;
    if (row.after === "—") return `Cleared ${row.key}`;
    if (row.before === "—") return `Set ${row.key} to ${row.after}`;
    return `Changed ${row.key} from ${row.before} to ${row.after}`;
  }
  return fallback.length > 240 || /[{}]/.test(fallback) ? "Review the recorded changes below." : fallback.trim();
}

export function JsonDiffView({ before, after, summary }: JsonDiffViewProps) {
  const parsedBefore = useMemo(() => parse(before), [before]);
  const parsedAfter = useMemo(() => parse(after), [after]);
  const opaque = useMemo(() => opaqueDetail(parsedBefore), [parsedBefore]);
  const projected = useMemo(
    () => (isEditorEnvelope(parsedAfter) ? projectEditorDiff(parsedBefore, parsedAfter) : null),
    [parsedBefore, parsedAfter],
  );
  const rows = useMemo(
    () => projected ?? diffFields(parsedBefore, parsedAfter),
    [projected, parsedBefore, parsedAfter],
  );
  const changed = rows.filter((r) => r.changed);
  const headline = projected ? editorStory(projected, summary) : editorStory([], summary);
  const result = recordedResult(parsedAfter);

  if (result) return (
    <div className="json-diff">
      <h3>{result.title}</h3>
      <dl className="ledger-facts">{result.facts.map(([label, value]) => <div key={label}><dt>{label}</dt><dd>{value}</dd></div>)}</dl>
      <p className="json-diff-note">This is the tool’s recorded response. It does not by itself describe everything that changed in the project.</p>
      <details className="ledger-technical"><summary>Technical details · full recorded data</summary>
        <h4>Before</h4><CodePane code={prettyCode(before) || "No earlier value recorded"} lang="json" />
        <h4>After</h4><CodePane code={prettyCode(after)} lang="json" />
      </details>
    </div>
  );

  if (opaque) {
    return (
      <div className="json-diff">
        {headline ? <p className="json-diff-summary">{headline}</p> : null}
        {opaque.code ? (
          <details className="json-diff-code ledger-technical">
            <summary>What ran · script or command</summary>
            <CodePane code={opaque.code} lang="python" />
          </details>
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

  const shown = projected ?? (changed.length > 0 ? changed : rows);
  const stacked = shown.some((row) => isStructuredText(row.before) || isStructuredText(row.after));
  const storyOnly = Boolean(projected && projected.length === 1 && headline && !stacked && shown[0].before.length + shown[0].after.length <= 240);

  return (
    <div className="json-diff">
      {headline ? <p className="json-diff-summary">{headline}</p> : null}
      {storyOnly ? null : shown.length > 0 && stacked ? (
        <div className="json-diff-fields">
          {shown.map((row) => (
            <article key={row.key} className={row.changed ? "json-diff-field json-diff-field--changed" : "json-diff-field"}>
              <h4>{readableLabel(row.key)}</h4>
              <div className="json-diff-sides">
                <section>
                  <h5>Before</h5>
                  <ValueView text={row.before} />
                </section>
                <section>
                  <h5>After</h5>
                  <ValueView text={row.after} />
                </section>
              </div>
            </article>
          ))}
        </div>
      ) : shown.length > 0 ? (
        <table className="json-diff-table">
          <thead>
            <tr>
              <th>Property</th>
              <th>Before</th>
              <th>After</th>
            </tr>
          </thead>
          <tbody>
            {shown.map((row) => (
              <tr key={row.key} className={row.changed ? "json-diff-row--changed" : ""}>
                <td className="json-diff-key">{readableLabel(row.key)}</td>
                <td className="json-diff-before"><ValueView text={row.before} /></td>
                <td className="json-diff-after"><ValueView text={row.after} /></td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : projected ? null : (
        <div className="json-diff-raw">
          <section>
            <h4>Before</h4>
            {isStructuredText(before) ? (
              <CodePane code={prettyCode(before)} lang="json" />
            ) : (
              <pre>{before || "—"}</pre>
            )}
          </section>
          <section>
            <h4>After</h4>
            {isStructuredText(after) ? (
              <CodePane code={prettyCode(after)} lang="json" />
            ) : (
              <pre>{after || "—"}</pre>
            )}
          </section>
        </div>
      )}
      {!projected && rows.length > 0 && changed.length === 0 ? (
        <p className="json-diff-note">Nothing changed between the recorded states.</p>
      ) : null}
    </div>
  );
}
