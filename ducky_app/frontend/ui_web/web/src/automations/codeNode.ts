import type {
  AutomationGraphDto,
  AutomationGraphEdgeDto,
  AutomationGraphNodeDto,
  CodeBasedOnDto,
  CodeCheckDto,
  CodePinsDto,
  PinDto,
  WorkflowNodeCodeDto,
} from "../types/panel";
import { cleanType, PIN_TYPE_LABELS, type NodePins } from "./pins";

/** Custom code nodes: a step (or value) that runs the JavaScript in its config. One
 *  workflow's copy only; built-ins keep running their own handlers. */
export const CODE_TYPE = "code.js";

/** What a new Custom code node starts with (the server fills in the same on save). */
export const BLANK_CODE = `// @ts-check
export const node = {
  kind: "step",
  inputs: [{ id: "text", type: "text", label: "Text" }],
  outputs: [{ id: "text", type: "text", label: "Text" }],
  settings: [],
  tools: [],
  builtins: [],
};

/** @param {Record<string, any>} input @param {import("ducky").Ducky} ducky */
export default async function run(input, ducky) {
  ducky.log("Got", input.text);
  return { text: input.text };
}
`;

/** The pins BLANK_CODE declares, so a new node draws right before its first save. */
export const BLANK_PINS: CodePinsDto = {
  exec: true,
  inputs: [{ id: "text", type: "text", label: "Text" }],
  outputs: [{ id: "text", type: "text", label: "Text" }],
};

/** The biggest code one node may hold (the server refuses to save more). */
export const MAX_CODE_BYTES = 64 * 1024;

export function isCodeNode(node: AutomationGraphNodeDto | undefined | null): boolean {
  return node?.type === CODE_TYPE;
}

export function blankCodeConfig(): Record<string, unknown> {
  return { code: BLANK_CODE, pins: structuredClone(BLANK_PINS), settings_spec: [], uses: { tools: [], builtins: [] }, settings: {}, inputs: {}, spend: false };
}

export function codeOf(node: AutomationGraphNodeDto): string {
  return typeof node.config.code === "string" && node.config.code ? node.config.code : BLANK_CODE;
}

/** The built-in a custom code node was made from, if any. */
export function basedOn(node: AutomationGraphNodeDto): CodeBasedOnDto | null {
  const raw = node.config.based_on;
  if (!raw || typeof raw !== "object") return null;
  const row = raw as Record<string, unknown>;
  const type = String(row.type || "");
  if (!type) return null;
  return { type, config: row.config && typeof row.config === "object" ? row.config as Record<string, unknown> : {}, ...(row.code_sha ? { code_sha: String(row.code_sha) } : {}) };
}

/** Edit as custom code: the built-in becomes a code.js node in this workflow only, running
 *  the code generated from its settings. Its typed-in input values and Spend switch stay. */
export function convertNode(node: AutomationGraphNodeDto, info: WorkflowNodeCodeDto): AutomationGraphNodeDto {
  const old = node.config || {};
  const config: Record<string, unknown> = {
    code: info.code,
    code_sha: info.code_sha,
    based_on: { type: node.type, config: structuredClone(old), code_sha: info.code_sha },
    inputs: old.inputs && typeof old.inputs === "object" ? structuredClone(old.inputs) : {},
    settings: {},
    spend: old.spend === true,
    pins: structuredClone(info.pins),
    settings_spec: structuredClone(info.settings_spec || []),
    uses: structuredClone(info.uses || { tools: [], builtins: [] }),
    problems: structuredClone(info.problems || []),
  };
  return { ...node, type: CODE_TYPE, config };
}

/** Revert to built-in: the type and settings it had when it was converted. */
export function revertNode(node: AutomationGraphNodeDto): AutomationGraphNodeDto | null {
  const from = basedOn(node);
  return from ? { ...node, type: from.type, config: structuredClone(from.config) } : null;
}

/** A check's answer applied to the node: its problems always; the pins, settings and tools
 *  only when the check passed, so a half-typed declaration keeps the last good pins. The
 *  hash is the server's to write when it saves. */
export function withCheck(node: AutomationGraphNodeDto, check: CodeCheckDto): AutomationGraphNodeDto {
  const config: Record<string, unknown> = { ...node.config, problems: check.problems || [] };
  if (check.ok) {
    config.pins = check.pins;
    config.settings_spec = check.settings_spec || [];
    config.uses = check.uses || { tools: [], builtins: [] };
  }
  return { ...node, config };
}

/** JSON with object keys sorted, so the same value always reads the same. */
function stableJson(value: unknown): string {
  if (Array.isArray(value)) return `[${value.map(stableJson).join(",")}]`;
  if (value && typeof value === "object") {
    return `{${Object.keys(value).filter((key) => (value as Record<string, unknown>)[key] !== undefined).sort().map((key) => `${JSON.stringify(key)}:${stableJson((value as Record<string, unknown>)[key])}`).join(",")}}`;
  }
  return JSON.stringify(value ?? null);
}

/** What the code declares that the canvas and the details draw (pins, settings, tools). */
export function declarationKey(node: AutomationGraphNodeDto): string {
  const uses = (node.config.uses ?? {}) as { tools?: unknown; builtins?: unknown };
  return stableJson([node.config.pins ?? null, node.config.settings_spec ?? [], uses.tools ?? [], uses.builtins ?? []]);
}

/** The data wires on `nodeId` whose pins `pins` no longer has. */
export function wiresDropped(graph: AutomationGraphDto, nodeId: string, pins: NodePins): AutomationGraphEdgeDto[] {
  return graph.edges.filter((edge) => edge.kind === "data" && (
    (edge.target === nodeId && !pins.inputs.some((pin) => pin.id === edge.target_pin))
    || (edge.source === nodeId && !pins.outputs.some((pin) => pin.id === edge.source_pin))));
}

export function dropWires(graph: AutomationGraphDto, dropped: AutomationGraphEdgeDto[]): AutomationGraphDto {
  return dropped.length ? { ...graph, edges: graph.edges.filter((edge) => !dropped.includes(edge)) } : graph;
}

/** "Prompt.text → Text to Image.prompt", with each node's name. */
export function wireText(edge: AutomationGraphEdgeDto, nameOf: (id: string) => string): string {
  return `${nameOf(edge.source)}.${edge.source_pin || ""} → ${nameOf(edge.target)}.${edge.target_pin || ""}`;
}

export type PinChange = { dir: "input" | "output"; pin: PinDto; was?: PinDto };
export type PinDiff = { added: PinChange[]; removed: PinChange[]; retyped: PinChange[]; exec?: { was: boolean; now: boolean } };

/** What changes on the card when `after` replaces `before`. */
export function pinDiff(before: NodePins, after: NodePins): PinDiff {
  const diff: PinDiff = { added: [], removed: [], retyped: [] };
  for (const dir of ["input", "output"] as const) {
    const was = dir === "input" ? before.inputs : before.outputs;
    const now = dir === "input" ? after.inputs : after.outputs;
    for (const pin of now) {
      const old = was.find((item) => item.id === pin.id);
      if (!old) diff.added.push({ dir, pin });
      else if (cleanType(old.type) !== cleanType(pin.type)) diff.retyped.push({ dir, pin, was: old });
    }
    for (const pin of was) if (!now.some((item) => item.id === pin.id)) diff.removed.push({ dir, pin });
  }
  if (before.exec !== after.exec) diff.exec = { was: before.exec, now: after.exec };
  return diff;
}

export function pinDiffEmpty(diff: PinDiff): boolean {
  return !diff.added.length && !diff.removed.length && !diff.retyped.length && !diff.exec;
}

/** One line per change, in plain words: "+ input “name” (Text)", "1 wire will be disconnected". */
export function pinDiffText(diff: PinDiff, dropped = 0): string[] {
  const label = (pin: PinDto) => PIN_TYPE_LABELS[cleanType(pin.type)];
  const lines = [
    ...diff.added.map(({ dir, pin }) => `+ ${dir} “${pin.id}” (${label(pin)})`),
    ...diff.removed.map(({ dir, pin }) => `− ${dir} “${pin.id}”`),
    ...diff.retyped.map(({ dir, pin, was }) => `${dir} “${pin.id}”: ${was ? label(was) : "?"} → ${label(pin)}`),
  ];
  if (diff.exec) lines.push(diff.exec.now ? "Becomes a step (white pins)" : "Becomes a value node (no white pins)");
  if (dropped) lines.push(`${dropped} wire${dropped === 1 ? "" : "s"} will be disconnected`);
  return lines;
}
