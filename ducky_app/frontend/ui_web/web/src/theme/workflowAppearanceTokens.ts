import type { ChatNumberToken } from "./chatAppearanceTokens";
import { colorToHexAndAlpha, hexToRgba } from "./colorUtils";

/** Workflow editor look (Settings → Appearance → Workflows). Every value is a CSS
 *  variable the canvas, list and details panel read; Auto follows the theme palette:
 *  the canvas is the same --bg the chats sit on, its grid lines are the text color, faint. */
type Vars = Record<string, string>;
export interface WorkflowColorToken {
  id: string;
  name: string;
  group: "canvas" | "nodes" | "wires" | "pins" | "list";
  auto: (vars: Vars) => string;
}
export type WorkflowNumberToken = ChatNumberToken;

const fixed = (value: string) => () => value;
const from = (id: string) => (vars: Vars) => vars[id]!;
const faint = (id: string, alpha: number) => (vars: Vars) => hexToRgba(colorToHexAndAlpha(vars[id] || "#ffffff").hex, alpha);

export const WORKFLOW_COLOR_TOKENS: WorkflowColorToken[] = [
  { id: "wf-canvas", name: "Canvas", group: "canvas", auto: from("bg") },
  { id: "wf-grid-minor", name: "Grid lines", group: "canvas", auto: faint("fg", 0.05) },
  { id: "wf-grid-major", name: "Grid major lines", group: "canvas", auto: faint("fg", 0.1) },
  { id: "wf-select", name: "Selection outline", group: "canvas", auto: from("amber") },
  // A run as it happens: the current step yellow, finished steps grey, the wires it took green.
  { id: "wf-run", name: "Current step", group: "canvas", auto: fixed("#facc15") },
  { id: "wf-run-done", name: "Finished step", group: "canvas", auto: fixed("#9ca3af") },
  { id: "wf-run-path", name: "Wires the run took", group: "canvas", auto: from("green") },
  { id: "wf-group", name: "Plain group frame", group: "canvas", auto: fixed("#8c8c8c") },
  // See-through panels over the canvas (list, top bar, details, bottom toolbar); see --wf-panel-blur.
  { id: "wf-panel", name: "Panels over the canvas", group: "canvas", auto: (vars) => hexToRgba(colorToHexAndAlpha(vars.bg!).hex, 0.58) },
  { id: "wf-node-body", name: "Node body", group: "nodes", auto: fixed("#141414") },
  { id: "wf-node-border", name: "Node border", group: "nodes", auto: fixed("#1c1c1c") },
  { id: "wf-node-text", name: "Node title", group: "nodes", auto: fixed("rgba(255, 255, 255, 0.93)") },
  { id: "wf-node-muted", name: "Node description", group: "nodes", auto: fixed("rgba(255, 255, 255, 0.58)") },
  { id: "wf-node-starter", name: "Starts (chat, schedule, inputs)", group: "nodes", auto: from("red") },
  { id: "wf-node-action", name: "Actions and tools", group: "nodes", auto: from("blue") },
  { id: "wf-node-function", name: "Run workflow", group: "nodes", auto: from("green") },
  { id: "wf-node-logic", name: "Flow control (branch, wait, loop)", group: "nodes", auto: fixed("#6b6b6b") },
  { id: "wf-node-agent", name: "Duckies and agents", group: "nodes", auto: from("amber") },
  { id: "wf-node-end", name: "Returns and previews", group: "nodes", auto: from("purple") },
  { id: "wf-node-input", name: "Inputs (text, numbers, files)", group: "nodes", auto: fixed("#1fa7a0") },
  { id: "wf-wire-true", name: "True route", group: "wires", auto: from("green") },
  { id: "wf-wire-false", name: "False route", group: "wires", auto: from("red") },
  { id: "wf-wire-each", name: "Each item route", group: "wires", auto: from("purple") },
  { id: "wf-wire-done", name: "Done route", group: "wires", auto: from("blue") },
  // Data pins and their wires, by what they carry (Unreal-style colours).
  { id: "wf-pin-text", name: "Text", group: "pins", auto: fixed("#e04fd8") },
  { id: "wf-pin-number", name: "Number", group: "pins", auto: fixed("#7bd34c") },
  { id: "wf-pin-boolean", name: "Yes/No", group: "pins", auto: fixed("#e0453b") },
  { id: "wf-pin-json", name: "Data", group: "pins", auto: fixed("#9aa4b2") },
  { id: "wf-pin-any", name: "Anything", group: "pins", auto: fixed("#d9d9d9") },
  { id: "wf-pin-image", name: "Images", group: "pins", auto: fixed("#2fc6e8") },
  { id: "wf-pin-audio", name: "Audio", group: "pins", auto: fixed("#ff9b3d") },
  { id: "wf-pin-video", name: "Video", group: "pins", auto: fixed("#a974ff") },
  { id: "wf-pin-mesh", name: "3D models", group: "pins", auto: fixed("#f2c94c") },
  { id: "wf-pin-file", name: "Files, PDFs, SVGs", group: "pins", auto: fixed("#4f8cff") },
  { id: "wf-light-on", name: "On light", group: "list", auto: from("green") },
  { id: "wf-light-off", name: "Off light", group: "list", auto: from("red") },
];

export const WORKFLOW_NUMBER_TOKENS: WorkflowNumberToken[] = [
  { id: "wf-panel-blur", name: "Panel blur", value: 28, unit: "px", min: 0, max: 60, step: 2 },
  { id: "wf-wire-width", name: "Wire thickness", value: 1.5, unit: "px", min: 0.5, max: 5, step: 0.25 },
  { id: "wf-wire-glow", name: "Wire glow width", value: 5, unit: "px", min: 0, max: 16, step: 1 },
  { id: "wf-node-title-size", name: "Node title size", value: 15, unit: "px", min: 10, max: 22, step: 1 },
  { id: "wf-node-text-size", name: "Node description size", value: 11.5, unit: "px", min: 9, max: 16, step: 0.5 },
  { id: "wf-node-mark-opacity", name: "Node icon in the background", value: 22, unit: "%", min: 0, max: 60, step: 2 },
  { id: "wf-grid-size", name: "Grid spacing", value: 16, unit: "px", min: 8, max: 48, step: 2 },
  { id: "wf-list-text-size", name: "Workflow list text size", value: 13, unit: "px", min: 11, max: 18, step: 0.5 },
];

export const WORKFLOW_COLOR_GROUPS: Array<{ id: WorkflowColorToken["group"]; name: string }> = [
  { id: "canvas", name: "Canvas" },
  { id: "nodes", name: "Nodes (their wires take the same color)" },
  { id: "wires", name: "Branch and loop wires" },
  { id: "pins", name: "Data pins and wires (by what they carry)" },
  { id: "list", name: "Workflow list" },
];

export const WORKFLOW_APPEARANCE_TOKEN_IDS = [
  ...WORKFLOW_COLOR_TOKENS.map((t) => t.id),
  ...WORKFLOW_NUMBER_TOKENS.map((t) => t.id),
];

/** Apply after the palette so Auto follows the active theme. */
export function applyWorkflowAppearance(vars: Vars, overrides: Vars): void {
  for (const token of WORKFLOW_COLOR_TOKENS) vars[token.id] = overrides[token.id] || token.auto(vars);
  for (const token of WORKFLOW_NUMBER_TOKENS) vars[token.id] = overrides[token.id] || `${token.value}${token.unit}`;
}
