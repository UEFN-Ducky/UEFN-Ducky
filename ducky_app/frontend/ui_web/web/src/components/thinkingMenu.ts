export type ThinkingMenuLevel = {
  id: string;
  label?: string;
  thinking_tokens?: number | null;
  hint?: string;
};

export type ThinkingMenu = {
  lo?: string;
  hi?: string;
  levels: ThinkingMenuLevel[];
};

export const DISABLED_THINKING_MENU: ThinkingMenu = {
  lo: "Faster",
  hi: "Smarter",
  levels: [
    { id: "off", label: "Off", thinking_tokens: 0, hint: "This model has no extended thinking" },
  ],
};

export function catalogThinkingMenu(
  row: { thinkingMenu?: ThinkingMenu | null } | null | undefined,
): ThinkingMenu | null {
  const levels = row?.thinkingMenu?.levels;
  if (!levels?.length) return null;
  return row!.thinkingMenu!;
}

/** Slider stays on screen: gateway menu, or a one-step Off rail. */
export function footerThinkingMenu(menu: ThinkingMenu | null | undefined): ThinkingMenu {
  return menu?.levels?.length ? menu : DISABLED_THINKING_MENU;
}

export function thinkingSliderEnabled(menu: ThinkingMenu | null | undefined): boolean {
  return (menu?.levels?.length ?? 0) > 1;
}

export function effortInMenu(menu: ThinkingMenu | null | undefined, effort: string): string {
  const v = (effort || "").trim().toLowerCase() || "off";
  if (!menu?.levels?.length) return "off";
  return menu.levels.some((l) => (l.id || "").toLowerCase() === v) ? v : "off";
}

export function menuLevel(menu: ThinkingMenu | null | undefined, effort: string): ThinkingMenuLevel | undefined {
  const id = effortInMenu(menu, effort);
  return menu?.levels.find((l) => (l.id || "").toLowerCase() === id) ?? menu?.levels[0];
}

export function formatTokenText(level: ThinkingMenuLevel | undefined): string {
  if (!level) return "0 thinking tokens";
  if (level.thinking_tokens == null) return "no token cap";
  return `${level.thinking_tokens} thinking tokens`;
}

export function formatEffortReadout(
  modelName: string,
  level: ThinkingMenuLevel | undefined,
): string {
  const label = (level?.label || level?.id || "Off").trim();
  const name = (modelName || "").trim();
  return name ? `${name} · ${label}` : label;
}

export function effortSuffix(menu: ThinkingMenu | null | undefined, effort: string): string {
  if (!menu?.levels?.length) return "";
  const level = menuLevel(menu, effort);
  const label = (level?.label || level?.id || "Off").trim();
  return label ? ` · ${label}` : "";
}
