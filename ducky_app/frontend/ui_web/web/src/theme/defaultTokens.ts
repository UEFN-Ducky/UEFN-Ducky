/** Default CSS custom property values (without leading --). Hex-only; rgba/shadows computed in tokenEngine. */
export const DEFAULT_CSS_VARS: Record<string, string> = {
  bg: "#0a0a0a",
  sidebar: "#121212",
  card: "#181818",
  "card-hover": "#222222",
  "panel-header": "#141414",
  "btn-bg": "#2a2a2a",
  "btn-hover": "#383838",
  "btn-pressed": "#1e1e1e",
  fg: "#ededed",
  "fg-dim": "#a1a1a1",
  muted: "#666666",
  border: "#262626",
  "border-light": "#333333",
  "border-focus": "#3b82f6",
  select: "#2b5d8e",
  green: "#3ddc84",
  red: "#ff6058",
  amber: "#e8a317",
  blue: "#5eb3f6",
  accent: "#2563eb",
  "accent-hover": "#1d4ed8",
  "dropdown-bg": "#181818",
  radius: "12px",
  "radius-sm": "8px",
  "font-ui": '"Inter", "Segoe UI", system-ui, sans-serif',
  "font-mono": '"JetBrains Mono", Consolas, monospace',
  "font-ui-size": "12px",
  "font-mono-size": "13px",
  "blur-md": "blur(12px)",
};

export type FoundationKey = "accent" | "bg" | "surface" | "text" | "border";

export const FOUNDATION_KEYS: FoundationKey[] = ["accent", "bg", "surface", "text", "border"];

export const FOUNDATION_LABELS: Record<FoundationKey, string> = {
  accent: "Accent",
  bg: "Background",
  surface: "Surface",
  text: "Text",
  border: "Border",
};

export function defaultFoundation(): Record<FoundationKey, string> {
  return {
    accent: DEFAULT_CSS_VARS.accent,
    bg: DEFAULT_CSS_VARS.bg,
    surface: DEFAULT_CSS_VARS.card,
    text: DEFAULT_CSS_VARS.fg,
    border: DEFAULT_CSS_VARS.border,
  };
}
