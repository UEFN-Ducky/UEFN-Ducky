import { useCallback, useEffect, useId, useRef, useState } from "react";
import { rankToolNames } from "../hooks/useToolSearch";
import { ChoiceDropdown, type ChoiceOption } from "../components/ChoiceDropdown";
import { getApi } from "../hooks/usePanelApi";
import { runBridgeJob } from "../hooks/bridgeJobAsync";
import type { AutomationGraphNodeDto, McpCatalogDto, McpToolDto, McpToolParameterDto } from "../types/panel";

/** One catalog for every Call tool node: the host tool list only changes when a
 *  plugin is toggled, so reopening the dropdown must not refetch it. */
const CATALOG_TTL_MS = 60_000;
const catalogCache: { at: number; tools: McpToolDto[]; pending: Promise<McpToolDto[]> | null } = { at: 0, tools: [], pending: null };

async function fetchWorkflowTools(): Promise<McpToolDto[]> {
  const api = getApi() as (Record<string, unknown> & { get_mcp_tools_catalog?: () => Promise<McpCatalogDto> }) | null;
  if (!api) throw new Error("Panel API unavailable");
  let catalog: McpCatalogDto | null = null;
  if (typeof api.get_workflow_tools_catalog === "function") {
    // Worker thread + poll: the bridge stays free and the list arrives in ms.
    catalog = await runBridgeJob<McpCatalogDto>("get_workflow_tools_catalog", [], 30_000);
  } else if (api.get_mcp_tools_catalog) {
    catalog = await api.get_mcp_tools_catalog(); // older app builds
  }
  if (!Array.isArray(catalog?.tools)) throw new Error("Tools unavailable");
  return catalog.tools;
}

export function loadWorkflowTools(force = false): Promise<McpToolDto[]> {
  const fresh = catalogCache.tools.length > 0 && Date.now() - catalogCache.at < CATALOG_TTL_MS;
  if (fresh && !force) return Promise.resolve(catalogCache.tools);
  if (catalogCache.pending) return catalogCache.pending;
  const pending = fetchWorkflowTools()
    .then((tools) => { catalogCache.tools = tools; catalogCache.at = Date.now(); return tools; })
    .finally(() => { if (catalogCache.pending === pending) catalogCache.pending = null; });
  catalogCache.pending = pending;
  return pending;
}

export function resetWorkflowToolsCache() { catalogCache.at = 0; catalogCache.tools = []; catalogCache.pending = null; }

function readArguments(node: AutomationGraphNodeDto): { values: Record<string, unknown>; valid: boolean; text: string } {
  const raw = node.config.arguments;
  const text = raw !== undefined ? JSON.stringify(raw, null, 2) : String(node.config.arguments_json || "");
  try {
    const parsed = raw !== undefined ? raw : text.trim() ? JSON.parse(text) : {};
    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) return { values: {}, valid: false, text };
    return { values: parsed, valid: true, text };
  } catch { return { values: {}, valid: false, text }; }
}

export function ToolSettings({ node, onChange }: { node: AutomationGraphNodeDto; onChange: (node: AutomationGraphNodeDto) => void }) {
  const [tools, setTools] = useState<McpToolDto[]>(() => catalogCache.tools);
  const [status, setStatus] = useState<"loading" | "ready" | "error">(() => (catalogCache.tools.length ? "ready" : "loading"));
  const request = useRef(0);
  const refresh = useCallback(async (force = false) => {
    const revision = ++request.current;
    try {
      const rows = await loadWorkflowTools(force);
      if (revision !== request.current) return;
      setTools(rows);
      setStatus("ready");
    } catch { if (revision === request.current) setStatus("error"); }
  }, []);
  useEffect(() => { void refresh(); return () => { request.current++; }; }, [refresh]);
  // Reopening only refetches after an error (cache is per TTL otherwise).
  const onOpen = useCallback(() => { void refresh(status === "error"); }, [refresh, status]);

  const name = String(node.config.name || "");
  const tool = tools.find((item) => item.name === name);
  const args = readArguments(node);
  const parameters = tool?.parameters || [];
  const options: ChoiceOption[] = tools.map((item) => ({ value: item.name, label: item.name, hint: item.description, group: item.category_label || "Tools" }));
  if (name && !tool) options.unshift({ value: name, label: name, hint: "Saved tool — not in the current catalog" });
  const setArgument = (parameter: string, value: unknown) => {
    const next = { ...args.values };
    if (value === undefined) delete next[parameter];
    else next[parameter] = value;
    const config: Record<string, unknown> = { ...node.config, arguments: next };
    delete config.arguments_json;
    onChange({ ...node, config });
  };
  // Saved inputs the tool doesn't list (all of them while its catalog is unavailable) keep a field too.
  const listed = new Set(parameters.map((parameter) => parameter.name));
  const extras = Object.keys(args.values).filter((key) => !listed.has(key));

  return <>
    <div className="aw-field">
      <span className="aw-field-label">Tool</span>
      <ChoiceDropdown aria-label="Tool" value={name} options={options} onChange={(next) => onChange({ ...node, config: { ...node.config, name: next } })} placeholder="Choose a tool…" searchable searchPlaceholder="Search tools" rankSearch={rankToolNames} showSelectedHint={false} onOpen={onOpen} size="compact" minWidth={300} emptyLabel={status === "loading" ? "Loading tools…" : "No tools available"} footer={status === "error" ? "Could not load tools — reopen to retry" : undefined} />
    </div>
    {args.valid ? <>
      {parameters.map((parameter) => <ToolArgument key={parameter.name} parameter={parameter} value={args.values[parameter.name]} onChange={(value) => setArgument(parameter.name, value)} />)}
      {extras.map((key) => <ToolArgument key={key} parameter={{ name: key, type: typeOf(args.values[key]), description: tool ? "This tool doesn't list this input." : "", required: false }}
        value={args.values[key]} onChange={(value) => setArgument(key, value)} removable />)}
      {tool && !parameters.length && !extras.length ? <p className="aw-field-hint">This tool needs no inputs.</p> : null}
    </> : <label className="aw-field">
      {/* Only for saved inputs that can't be read: fix them here to get the fields back. */}
      <span className="aw-field-label">Inputs</span>
      <textarea aria-label="Arguments JSON" className="aw-json-input" rows={4} value={args.text} placeholder="{}" spellCheck={false} onChange={(event) => {
        const config: Record<string, unknown> = { ...node.config, arguments_json: event.target.value };
        delete config.arguments;
        onChange({ ...node, config });
      }} />
      <p className="aw-field-error" role="status">These saved inputs aren't valid JSON. Fix them to get the input fields back.</p>
    </label>}
  </>;
}

function typeOf(value: unknown): string {
  if (typeof value === "number") return "number";
  if (typeof value === "boolean") return "boolean";
  if (Array.isArray(value)) return "array";
  if (value && typeof value === "object") return "object";
  return "string";
}

/** Typed text as a value: a number where one fits, else the text itself (e.g. "{{score}}" from an earlier step). */
function readNumber(text: string): unknown {
  const trimmed = text.trim();
  if (!trimmed) return undefined;
  const number = Number(trimmed);
  return Number.isFinite(number) ? number : text;
}

/** An object or list input: its own small JSON box. The value only changes once the text is valid. */
function JsonArgument({ id, label, value, onChange }: { id: string; label: string; value: unknown; onChange: (value: unknown) => void }) {
  const shown = value === undefined ? "" : JSON.stringify(value, null, 2);
  const [text, setText] = useState(shown);
  const [error, setError] = useState(false);
  useEffect(() => {
    setText((current) => { try { return current.trim() && JSON.stringify(JSON.parse(current)) === JSON.stringify(value) ? current : shown; } catch { return current; } });
  }, [shown]);
  return <>
    <textarea id={id} aria-label={label} className="aw-json-input" rows={3} value={text} placeholder="{}" spellCheck={false} onChange={(event) => {
      const next = event.target.value;
      setText(next);
      if (!next.trim()) { setError(false); onChange(undefined); return; }
      try { onChange(JSON.parse(next)); setError(false); } catch { setError(true); }
    }} />
    {error ? <small className="aw-field-error" role="status">Not valid JSON yet.</small> : null}
  </>;
}

function ToolArgument({ parameter, value, onChange, removable }: { parameter: McpToolParameterDto; value: unknown; onChange: (value: unknown) => void; removable?: boolean }) {
  const id = useId();
  const label = parameter.name.replace(/_/g, " ");
  const numeric = parameter.type === "number" || parameter.type === "integer";
  const structured = value != null ? typeof value === "object" : parameter.type === "object" || parameter.type === "array";
  // A yes/no input holding a value from an earlier step ("{{flag}}") is shown as that text.
  const choice = parameter.type === "boolean" && (value == null || typeof value === "boolean");
  const defaultLabel = parameter.default === undefined || parameter.default === null ? "Use default" : `Default: ${String(parameter.default)}`;
  return <div className="aw-field">
    <label className="aw-field-label" htmlFor={id}>{label}{parameter.required ? <span className="aw-required" title="Required"> *</span> : null}</label>
    {structured ? <JsonArgument id={id} label={label} value={value} onChange={onChange} />
      : choice ? <ChoiceDropdown id={id} aria-label={label} value={value == null ? "" : String(value)} options={[{ value: "", label: defaultLabel }, { value: "true", label: "Yes" }, { value: "false", label: "No" }]} onChange={(next) => onChange(next === "" ? undefined : next === "true")} size="compact" />
      : numeric ? <NumberArgument id={id} label={label} value={value} placeholder={parameter.default == null ? "" : String(parameter.default)} onChange={onChange} />
      : <input id={id} aria-label={label} type="text" value={value == null ? "" : String(value)} placeholder={parameter.default == null ? "" : String(parameter.default)} onChange={(event) => { const next = event.target.value; onChange(next === "" ? undefined : next); }} />}
    {parameter.description || removable ? <small className="aw-field-hint">{parameter.description}{removable ? <> <button type="button" className="aw-link" onClick={() => onChange(undefined)}>Remove</button></> : null}</small> : null}
  </div>;
}

/** A number, or a value from an earlier step ("{{score}}"). Half-typed numbers ("3.") stay as typed. */
function NumberArgument({ id, label, value, placeholder, onChange }: { id: string; label: string; value: unknown; placeholder: string; onChange: (value: unknown) => void }) {
  const shown = value == null ? "" : String(value);
  const [text, setText] = useState(shown);
  useEffect(() => { setText((current) => readNumber(current) === (value ?? undefined) ? current : shown); }, [shown]);
  return <input id={id} aria-label={label} type="text" inputMode="decimal" value={text} placeholder={placeholder} onChange={(event) => { setText(event.target.value); onChange(readNumber(event.target.value)); }} />;
}
