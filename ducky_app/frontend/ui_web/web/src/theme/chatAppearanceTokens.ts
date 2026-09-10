import { colorToHexAndAlpha, parseHexColor, parseRgba } from "./colorUtils";

type Vars = Record<string, string>;
export interface ChatColorToken {
  id: string;
  name: string;
  group: "text" | "chips" | "blocks" | "callouts";
  auto: (vars: Vars) => string;
}
export interface ChatNumberToken {
  id: string;
  name: string;
  value: number;
  unit: string;
  min: number;
  max: number;
  step: number;
}

// Match CSS color-mix defaults while giving the color picker an editable RGBA value.
function mix(a: string, b: string, weight: number): string {
  const parse = (value: string) => parseRgba(value) ?? parseHexColor(colorToHexAndAlpha(value).hex)!;
  const first = parse(a), second = parse(b);
  const alpha = first.a * weight + second.a * (1 - weight);
  const channel = (key: "r" | "g" | "b") => alpha ? Math.round((first[key] * first.a * weight + second[key] * second.a * (1 - weight)) / alpha) : 0;
  return `rgba(${channel("r")}, ${channel("g")}, ${channel("b")}, ${alpha})`;
}
const tint = (color: string, alpha: number) => mix(color, "rgba(0, 0, 0, 0)", alpha);
const from = (id: string) => (vars: Vars) => vars[id]!;

export const CHAT_COLOR_TOKENS: ChatColorToken[] = [
  { id: "chat-body-color", name: "Response text", group: "text", auto: from("fg-dim") },
  { id: "chat-list-color", name: "List text", group: "text", auto: from("fg-dim") },
  { id: "chat-heading-color", name: "Heading text", group: "text", auto: from("fg") },
  { id: "chat-emphasis-color", name: "Default emphasis", group: "text", auto: from("fg") },
  { id: "chat-muted-color", name: "Labels & metadata", group: "text", auto: from("muted") },
  { id: "chat-link-color", name: "File & web links", group: "text", auto: from("accent") },
  { id: "chat-border-color", name: "Block borders & dividers", group: "blocks", auto: from("border") },
  { id: "chat-surface", name: "Block surface", group: "blocks", auto: from("card") },
  { id: "chat-code-color", name: "Default code badge", group: "chips", auto: from("purple") },
  { id: "chat-ref-verse", name: "Verse files", group: "chips", auto: from("purple") },
  { id: "chat-ref-keyword", name: "Verse keywords", group: "chips", auto: from("purple") },
  { id: "chat-ref-file", name: "Project files", group: "chips", auto: from("blue") },
  { id: "chat-ref-tool", name: "Tools", group: "chips", auto: from("blue") },
  { id: "chat-ref-device", name: "Devices", group: "chips", auto: from("blue") },
  { id: "chat-ref-folder", name: "Folders & paths", group: "chips", auto: from("fg-dim") },
  { id: "chat-ref-actor", name: "Level actors", group: "chips", auto: from("fg") },
  { id: "chat-ref-field", name: "Fields & labels", group: "chips", auto: from("yellow") },
  { id: "chat-ref-prefab", name: "Prefabs", group: "chips", auto: from("amber") },
  { id: "chat-ref-asset", name: "Assets", group: "chips", auto: from("amber") },
  { id: "chat-ref-mesh", name: "Meshes & Blender", group: "chips", auto: from("green") },
  { id: "chat-ref-umg", name: "UMG", group: "chips", auto: from("yellow") },
  { id: "chat-ref-name", name: "Other names", group: "chips", auto: from("muted") },
  { id: "chat-code-background", name: "Code block background", group: "blocks", auto: from("card") },
  { id: "chat-table-heading-color", name: "Table heading text", group: "blocks", auto: from("blue") },
  { id: "chat-table-heading-background", name: "Table heading background", group: "blocks", auto: (v) => mix(v.blue!, v.card!, 0.08) },
  { id: "chat-stats-number-color", name: "Summary numbers", group: "blocks", auto: from("fg") },
  ...([ ["info", "Note", "blue", 0.1, 0.3], ["warn", "Warning", "amber", 0.12, 0.35], ["error", "Error", "red", 0.12, 0.35], ["success", "Success", "green", 0.12, 0.35] ] as const).flatMap(([tone, label, color, bg, border]): ChatColorToken[] => [
    { id: `chat-callout-${tone}-color`, name: `${label} accent`, group: "callouts", auto: from(color) },
    { id: `chat-callout-${tone}-background`, name: `${label} background`, group: "callouts", auto: (v) => tint(v[`chat-callout-${tone}-color`]!, bg) },
    { id: `chat-callout-${tone}-border`, name: `${label} border`, group: "callouts", auto: (v) => mix(v[`chat-callout-${tone}-color`]!, v["chat-border-color"]!, border) },
  ]),
];

export const CHAT_NUMBER_TOKENS: ChatNumberToken[] = [
  { id: "chat-text-size", name: "Message size", value: 14, unit: "px", min: 10, max: 28, step: 1 },
  { id: "chat-line-height", name: "Text line height", value: 1.8, unit: "", min: 1.2, max: 2.5, step: 0.05 },
  { id: "chat-h1-size", name: "Title size", value: 1.25, unit: "em", min: 1, max: 2.5, step: 0.05 },
  { id: "chat-h2-size", name: "Section heading size", value: 1.05, unit: "em", min: 0.9, max: 2, step: 0.05 },
  { id: "chat-h3-size", name: "Subheading size", value: 1, unit: "em", min: 0.85, max: 1.8, step: 0.05 },
  { id: "chat-h4-size", name: "Small heading size", value: 0.92, unit: "em", min: 0.8, max: 1.6, step: 0.02 },
  { id: "chat-heading-weight", name: "Heading weight", value: 600, unit: "", min: 300, max: 800, step: 100 },
  { id: "chat-paragraph-gap", name: "Paragraph spacing", value: 0.85, unit: "em", min: 0, max: 2, step: 0.05 },
  { id: "chat-section-gap", name: "Section spacing", value: 1.4, unit: "em", min: 0.4, max: 3, step: 0.1 },
  { id: "chat-inventory-gap", name: "Inventory row spacing", value: 1.15, unit: "em", min: 0.4, max: 2.5, step: 0.05 },
  { id: "chat-block-radius", name: "Block corners", value: 8, unit: "px", min: 0, max: 24, step: 1 },
  { id: "chat-code-tint", name: "Code badge tint", value: 7, unit: "%", min: 0, max: 40, step: 1 },
  { id: "chat-code-border-tint", name: "Code badge border tint", value: 12, unit: "%", min: 0, max: 60, step: 1 },
  { id: "chat-stats-size", name: "Summary number size", value: 3.5, unit: "em", min: 1, max: 4, step: 0.05 },
];

export const CHAT_COLOR_GROUPS: Array<{ id: ChatColorToken["group"]; name: string }> = [
  { id: "text", name: "Text" },
  { id: "chips", name: "Code badges" },
  { id: "blocks", name: "Blocks" },
  { id: "callouts", name: "Callouts" },
];

export const CHAT_APPEARANCE_TOKEN_IDS = [
  ...CHAT_COLOR_TOKENS.map((t) => t.id),
  ...CHAT_NUMBER_TOKENS.map((t) => t.id),
];

/** Apply after global palette, fonts and status colors so Auto follows the active theme. */
export function applyChatAppearance(vars: Vars, overrides: Vars): void {
  for (const token of CHAT_COLOR_TOKENS) vars[token.id] = overrides[token.id] || token.auto(vars);
  for (const token of CHAT_NUMBER_TOKENS) {
    vars[token.id] = overrides[token.id] || (token.id === "chat-block-radius" ? vars["radius-sm"] : `${token.value}${token.unit}`)!;
  }
}
