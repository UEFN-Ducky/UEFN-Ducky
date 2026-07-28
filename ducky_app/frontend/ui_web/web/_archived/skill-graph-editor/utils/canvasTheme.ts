/** Canvas colors resolved from appearance CSS variables on the graph container. */

export interface SpsCanvasTheme {
  bg: string;
  grid: string;
  edge: string;
  nodeFill: string;
  nodeFillDefault: string;
  nodeStroke: string;
  nodeStrokeActive: string;
  nodeStrokeDefault: string;
  shadow: string;
  shadowActive: string;
  shadowDefault: string;
  divider: string;
  iconBg: string;
  iconColor: string;
  title: string;
  muted: string;
  chipBg: string;
  chipStroke: string;
  btnBg: string;
  btnStroke: string;
  chevron: string;
}

function cssVar(el: HTMLElement, name: string): string {
  return getComputedStyle(el).getPropertyValue(name).trim();
}

function firstVar(el: HTMLElement, names: string[]): string {
  for (const name of names) {
    const v = cssVar(el, name);
    if (v) return v;
  }
  return "";
}

export function readSpsCanvasTheme(container: HTMLElement): SpsCanvasTheme {
  return {
    bg: firstVar(container, ["--sps-canvas-bg", "--bg"]),
    grid: firstVar(container, ["--sps-canvas-grid", "--border"]),
    edge: firstVar(container, ["--sps-canvas-edge", "--border-light", "--border"]),
    nodeFill: firstVar(container, ["--sps-canvas-node-fill", "--card", "--input-bg"]),
    nodeFillDefault: firstVar(container, ["--sps-canvas-node-fill-default", "--green-dim", "--card"]),
    nodeStroke: firstVar(container, ["--sps-canvas-node-stroke", "--border"]),
    nodeStrokeActive: firstVar(container, ["--sps-canvas-node-stroke-active", "--accent"]),
    nodeStrokeDefault: firstVar(container, ["--sps-canvas-node-stroke-default", "--green"]),
    shadow: firstVar(container, ["--sps-canvas-shadow", "--border"]),
    shadowActive: firstVar(container, ["--sps-canvas-shadow-active", "--accent"]),
    shadowDefault: firstVar(container, ["--sps-canvas-shadow-default", "--green"]),
    divider: firstVar(container, ["--sps-canvas-divider", "--border"]),
    iconBg: firstVar(container, ["--sps-canvas-icon-bg", "--green-dim"]),
    iconColor: firstVar(container, ["--sps-canvas-icon-color", "--green"]),
    title: firstVar(container, ["--sps-canvas-title", "--fg"]),
    muted: firstVar(container, ["--sps-canvas-muted", "--muted", "--fg-dim"]),
    chipBg: firstVar(container, ["--sps-canvas-chip-bg", "--bg"]),
    chipStroke: firstVar(container, ["--sps-canvas-chip-stroke", "--border"]),
    btnBg: firstVar(container, ["--sps-canvas-btn-bg", "--btn-bg"]),
    btnStroke: firstVar(container, ["--sps-canvas-btn-stroke", "--bg"]),
    chevron: firstVar(container, ["--sps-canvas-chevron", "--fg-dim", "--muted"]),
  };
}
