/** UI-region groupings for the Appearance settings tab (each maps to a collapsible section). */

import { LAYOUT_TOKENS, STATUS_COLORS } from "./tokenEngine";
import { VERSE_COLOR_TOKENS } from "./verseSyntaxTokens";

export type AppearanceSectionKind =
  | "effects"
  | "sounds"
  | "tokens"
  | "semantic"
  | "status"
  | "layout";

export interface AppearanceFontToken {
  id: string;
  role: "ui" | "mono";
  hint: string;
}

export interface AppearanceUiSection {
  id: string;
  title: string;
  subtitle: string;
  defaultOpen?: boolean;
  kind?: AppearanceSectionKind;
  /** Color token ids from COLOR_TOKENS / TERMINAL_COLOR_TOKENS. */
  tokenIds?: string[];
  /** Per-section font override (default stack or a font from the library above). */
  fontToken?: AppearanceFontToken;
}

export const SEMANTIC_PALETTE_TOKENS: Array<{ id: string; name: string }> = [
  { id: "green", name: "Green" },
  { id: "green-dim", name: "Green Dim" },
  { id: "red", name: "Red" },
  { id: "red-dim", name: "Red Dim" },
  { id: "amber", name: "Amber" },
  { id: "amber-dim", name: "Amber Dim" },
  { id: "blue", name: "Blue" },
  { id: "blue-dim", name: "Blue Dim" },
  { id: "on-green", name: "On Green" },
  { id: "on-amber", name: "On Amber" },
];

export const APPEARANCE_UI_SECTIONS: AppearanceUiSection[] = [
  {
    id: "effects",
    title: "Effects",
    subtitle: "Fullscreen background animation.",
    kind: "effects",
  },
  {
    id: "sounds",
    title: "Sounds",
    subtitle: "Map events to built-in, plugin, or custom audio.",
    kind: "sounds",
  },
  {
    id: "shell",
    title: "App shell",
    subtitle: "Background, text, borders, and overlays.",
    defaultOpen: true,
    kind: "tokens",
    tokenIds: [
      "bg",
      "fg",
      "fg-dim",
      "fg-inverse",
      "muted",
      "border",
      "border-light",
      "overlay",
      "overlay-light",
      "bg-panel",
      "bg-elevated",
      "dropdown-bg",
    ],
  },
  {
    id: "header",
    title: "Header & title bar",
    subtitle: "Top bar and window controls.",
    kind: "tokens",
    fontToken: {
      id: "font-header",
      role: "ui",
      hint: "Title bar and header controls. Defaults to the app UI font.",
    },
    tokenIds: ["header", "panel-header", "btn-bg", "btn-hover", "btn-pressed"],
  },
  {
    id: "sidebar",
    title: "Sidebar",
    subtitle: "Chat list, file tree, and navigation.",
    kind: "tokens",
    fontToken: {
      id: "font-sidebar",
      role: "ui",
      hint: "Duckies and Content sidebar panels. Defaults to the app UI font.",
    },
    tokenIds: ["sidebar", "card", "card-hover", "bg-hover", "tab-active", "tab-hover"],
  },
  {
    id: "chat",
    title: "Chat",
    subtitle: "Composer, messages, and panels.",
    kind: "tokens",
    fontToken: {
      id: "chat-text-font",
      role: "ui",
      hint: "Chat message text. Defaults to the app UI font.",
    },
    tokenIds: ["input-bg"],
  },
  {
    id: "settings",
    title: "Modals & overlays",
    subtitle: "Dialogs and settings chrome.",
    kind: "tokens",
    fontToken: {
      id: "font-settings",
      role: "ui",
      hint: "Settings and modal chrome. Defaults to the app UI font.",
    },
    tokenIds: ["overlay", "overlay-light"],
  },
  {
    id: "controls",
    title: "Buttons & accent",
    subtitle: "Accent, links, and focus rings.",
    kind: "tokens",
    fontToken: {
      id: "font-controls",
      role: "ui",
      hint: "Buttons and accent controls. Defaults to the app UI font.",
    },
    tokenIds: ["accent", "accent-hover", "select", "border-focus"],
  },
  {
    id: "terminal",
    title: "Terminal",
    subtitle: "Embedded terminal colors.",
    kind: "tokens",
    fontToken: {
      id: "font-mono",
      role: "mono",
      hint: "Terminal and code surfaces. Defaults to JetBrains Mono.",
    },
    tokenIds: ["terminal-bg", "terminal-fg", "terminal-cursor", "terminal-selection"],
  },
  {
    id: "groupchat",
    title: "Discord chat",
    subtitle: "Discord Ducky panel colors.",
    kind: "tokens",
    tokenIds: [
      "groupchat-accent",
      "groupchat-bg",
      "groupchat-hover",
      "groupchat-time",
      "groupchat-composer-bg",
    ],
  },
  {
    id: "semantic",
    title: "Status & semantic colors",
    subtitle: "Success, error, warning, and info.",
    kind: "semantic",
  },
  {
    id: "status",
    title: "Status message cards",
    subtitle: "Banner and alert tints.",
    kind: "status",
  },
  {
    id: "layout",
    title: "Layout",
    subtitle: "Radius, shadows, and blur.",
    kind: "layout",
  },
];

export const VERSE_SECTION_TOKEN_IDS = VERSE_COLOR_TOKENS.map((t) => t.id);

export function sectionResetTokenIds(section: AppearanceUiSection): string[] {
  const fontId = section.fontToken?.id;
  if (section.kind === "layout") {
    return LAYOUT_TOKENS.filter((t) => t.id !== "font-ui" && t.id !== "font-mono").map((t) => t.id);
  }
  if (section.kind === "semantic") return SEMANTIC_PALETTE_TOKENS.map((t) => t.id);
  if (section.kind === "status" || section.kind === "effects" || section.kind === "sounds") {
    return [];
  }
  const colorIds = section.tokenIds ?? [];
  return fontId ? [fontId, ...colorIds] : colorIds;
}

export function sectionHasCustomOverrides(
  section: AppearanceUiSection,
  overrides: Record<string, string>,
  statusOverrides: Record<string, unknown>,
): boolean {
  if (section.kind === "effects" || section.kind === "sounds") return false;
  if (section.kind === "status") {
    return STATUS_COLORS.some((s) => statusOverrides[s.id]);
  }
  const fontCustom = section.fontToken?.id ? !!overrides[section.fontToken.id] : false;
  return fontCustom || sectionResetTokenIds(section).some((id) => id !== section.fontToken?.id && overrides[id]);
}
