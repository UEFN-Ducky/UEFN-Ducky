/** Read appearance CSS custom properties from the DOM. */

export function readCssVar(name: string, el: HTMLElement = document.documentElement): string {
  const key = name.startsWith("--") ? name : `--${name}`;
  return getComputedStyle(el).getPropertyValue(key).trim();
}

export function readCssVars(names: string[], el?: HTMLElement): string[] {
  return names.map((name) => readCssVar(name, el));
}

export function readTerminalTheme(el?: HTMLElement): {
  background: string;
  foreground: string;
  cursor: string;
  selectionBackground: string;
} {
  return {
    background: readCssVar("terminal-bg", el),
    foreground: readCssVar("terminal-fg", el),
    cursor: readCssVar("terminal-cursor", el),
    selectionBackground: readCssVar("terminal-selection", el),
  };
}

export function readConfettiPalette(el?: HTMLElement): string[] {
  return readCssVars(["red", "accent-hover", "amber", "red", "green", "fg", "amber", "accent"], el).filter(
    Boolean,
  );
}
