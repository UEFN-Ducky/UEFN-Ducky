import type { AutomationGraphNodeDto, AutomationNodeDto, AutomationSummaryDto, PinDto, PinType } from "../types/panel";
import { NODE_HEIGHT, NODE_HEIGHT_COMPACT, NODE_TITLE_HEIGHT, NODE_WIDTH, PORT_Y } from "./graphGeometry";

/** Typed pins on the canvas. Mirrors backend/automations/pins.py; keep the two in step. */

export const PIN_TYPES: PinType[] = ["text", "number", "boolean", "json", "any", "image", "images", "audio", "video", "mesh", "svg", "pdf", "file"];
const FILE_TYPES = new Set<PinType>(["image", "audio", "video", "mesh", "svg", "pdf", "file"]);
export const PIN_TYPE_LABELS: Record<PinType, string> = {
  text: "Text", number: "Number", boolean: "Yes/No", json: "Data", any: "Anything", image: "Image", images: "Images",
  audio: "Audio", video: "Video", mesh: "3D model", svg: "SVG", pdf: "PDF", file: "File",
};
const NAMED_INPUTS: Record<string, { type: PinType; defaults: string[] }> = {
  "logic.if": { type: "any", defaults: ["value"] },
  "logic.expression": { type: "any", defaults: ["a", "b"] },
  "text.template": { type: "text", defaults: ["a", "b"] },
  "list.make": { type: "any", defaults: ["a", "b"] },
};
/** Nodes whose `names` add one output each (Extract data: one per field). */
const NAMED_OUTPUTS: Record<string, PinType> = { "llm.extract": "any" };

export function cleanType(raw: unknown): PinType {
  const value = String(raw || "").trim().toLowerCase() as PinType;
  return PIN_TYPES.includes(value) ? value : "any";
}

/** Whether an output of type `source` may feed an input of type `target`. */
export function accepts(target: PinType, source: PinType): boolean {
  if (target === source || target === "any" || target === "json" || source === "any") return true;
  if (target === "text") return source === "number" || source === "boolean";
  if (target === "images") return source === "image";
  if (target === "file") return FILE_TYPES.has(source);
  return false;
}

function names(raw: unknown, key = "name"): string[] {
  const out: string[] = [];
  for (const row of Array.isArray(raw) ? raw : []) {
    const name = String((row && typeof row === "object" ? (row as Record<string, unknown>)[key] : row) ?? "").trim();
    if (name && !out.includes(name)) out.push(name);
  }
  return out;
}

function typesOf(raw: unknown): Record<string, PinType> {
  const out: Record<string, PinType> = {};
  for (const row of Array.isArray(raw) ? raw : []) if (row && typeof row === "object") out[String((row as { name?: unknown }).name ?? "").trim()] = cleanType((row as { type?: unknown }).type);
  return out;
}

export type NodePins = { exec: boolean; inputs: PinDto[]; outputs: PinDto[] };

/** Pins of one node on the graph: the type's own, or worked out from its settings. */
export function nodePins(node: AutomationGraphNodeDto, meta: AutomationNodeDto | undefined, workflows: AutomationSummaryDto[] = []): NodePins {
  let inputs = (meta?.inputs || []).map((pin) => ({ ...pin, type: cleanType(pin.type) }));
  let outputs = (meta?.outputs || []).map((pin) => ({ ...pin, type: cleanType(pin.type) }));
  const config = node.config || {};
  if (node.type === "flow.input") {
    const types = typesOf(config.inputs);
    outputs = names(config.inputs).map((name) => ({ id: name, label: name, type: types[name] || "any" }));
  } else if (node.type === "flow.output") {
    const types = typesOf(config.outputs);
    inputs = names(config.outputs).map((name) => ({ id: name, label: name, type: types[name] || "any" }));
  } else if (node.type === "workflow.call") {
    const signature = workflows.find((row) => row.id === String(config.workflow_id || ""))?.signature;
    inputs = (signature?.inputs || []).map((row) => ({ id: row.name, label: row.name, type: cleanType(row.type), ...(row.default ? { default: row.default } : {}) }));
    outputs = (signature?.outputs || []).map((name) => ({ id: name, label: name, type: cleanType(signature?.output_types?.[name]) }));
  } else if (NAMED_INPUTS[node.type]) {
    const spec = NAMED_INPUTS[node.type];
    inputs = (names(config.names).length ? names(config.names) : spec.defaults).map((name) => ({ id: name, label: name, type: spec.type }));
  } else if (NAMED_OUTPUTS[node.type]) {
    const taken = new Set(outputs.map((pin) => pin.id));
    outputs = [...outputs, ...names(config.names).filter((name) => !taken.has(name)).map((name) => ({ id: name, label: name, type: NAMED_OUTPUTS[node.type] }))];
  }
  return { exec: meta?.exec !== false, inputs, outputs };
}

/** Card layout in canvas units: title, the white-pin row, then one row per pin pair. */
export const PIN_ROW = 24;
const EXEC_ROW = 36;
const DATA_TOP_GAP = 6;
const BOTTOM_PAD = 10;

export type NodeLayout = { height: number; rows: number; execY: number; inputY: Record<string, number>; outputY: Record<string, number>; hasPins: boolean };

/** `extra`: room under the rows for what the node shows (a Preview). */
export function nodeLayout(pins: NodePins, compact = false, extra = 0): NodeLayout {
  const rows = Math.max(pins.inputs.length, pins.outputs.length);
  const hasPins = rows > 0;
  if (compact) {
    const mid = NODE_HEIGHT_COMPACT / 2;
    return { height: NODE_HEIGHT_COMPACT, rows, execY: mid, hasPins, inputY: Object.fromEntries(pins.inputs.map((pin) => [pin.id, mid])), outputY: Object.fromEntries(pins.outputs.map((pin) => [pin.id, mid])) };
  }
  if (!hasPins) return { height: NODE_HEIGHT + extra, rows, execY: PORT_Y, hasPins, inputY: {}, outputY: {} };
  const top = NODE_TITLE_HEIGHT + (pins.exec ? EXEC_ROW : DATA_TOP_GAP);
  const rowY = (index: number) => top + index * PIN_ROW + PIN_ROW / 2;
  return {
    height: top + rows * PIN_ROW + BOTTOM_PAD + extra,
    rows,
    execY: NODE_TITLE_HEIGHT + EXEC_ROW / 2,
    hasPins,
    inputY: Object.fromEntries(pins.inputs.map((pin, index) => [pin.id, rowY(index)])),
    outputY: Object.fromEntries(pins.outputs.map((pin, index) => [pin.id, rowY(index)])),
  };
}

/** Where a data pin sits on the canvas. */
export function pinPoint(node: AutomationGraphNodeDto, layout: NodeLayout, dir: "in" | "out", pin: string) {
  const y = (dir === "in" ? layout.inputY[pin] : layout.outputY[pin]) ?? layout.execY;
  return { x: node.x + (dir === "out" ? NODE_WIDTH : 0), y: node.y + y };
}

/** A short read-only value for a pin label on the card ("Hello…", 3, Yes, duck.png). */
export function shortValue(value: unknown): string {
  if (value === undefined || value === null || value === "") return "";
  if (typeof value === "boolean") return value ? "Yes" : "No";
  if (typeof value === "number") return String(value);
  if (typeof value === "string") return value.length > 22 ? `${value.slice(0, 21)}…` : value;
  if (Array.isArray(value)) return `${value.length} item${value.length === 1 ? "" : "s"}`;
  if (typeof value === "object" && "name" in (value as object)) return String((value as { name?: unknown }).name || "");
  return "…";
}

/** The first pin on `pins` a wire of `type` fits (inputs when wiring from an output). */
export function firstFit(pins: PinDto[], type: PinType, into: boolean): PinDto | undefined {
  return pins.find((pin) => into ? accepts(cleanType(pin.type), type) : accepts(type, cleanType(pin.type)));
}
