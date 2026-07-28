export function hexToHSL(hex: string): [number, number, number] {
  let h = hex.replace("#", "");
  let r = 0;
  let g = 0;
  let b = 0;
  if (h.length === 3) {
    r = parseInt(h[0] + h[0], 16);
    g = parseInt(h[1] + h[1], 16);
    b = parseInt(h[2] + h[2], 16);
  } else if (h.length === 6) {
    r = parseInt(h.slice(0, 2), 16);
    g = parseInt(h.slice(2, 4), 16);
    b = parseInt(h.slice(4, 6), 16);
  }
  r /= 255;
  g /= 255;
  b /= 255;
  const cmin = Math.min(r, g, b);
  const cmax = Math.max(r, g, b);
  const delta = cmax - cmin;
  let hue = 0;
  let s = 0;
  const l = (cmax + cmin) / 2;
  if (delta !== 0) {
    s = delta / (1 - Math.abs(2 * l - 1));
    if (cmax === r) hue = ((g - b) / delta) % 6;
    else if (cmax === g) hue = (b - r) / delta + 2;
    else hue = (r - g) / delta + 4;
    hue = Math.round(hue * 60);
    if (hue < 0) hue += 360;
  }
  return [hue, +(s * 100).toFixed(1), +(l * 100).toFixed(1)];
}

export function hslToHex(h: number, s: number, l: number): string {
  s /= 100;
  l /= 100;
  const c = (1 - Math.abs(2 * l - 1)) * s;
  const x = c * (1 - Math.abs(((h / 60) % 2) - 1));
  const m = l - c / 2;
  let r = 0;
  let g = 0;
  let b = 0;
  if (h >= 0 && h < 60) {
    r = c;
    g = x;
  } else if (h >= 60 && h < 120) {
    r = x;
    g = c;
  } else if (h >= 120 && h < 180) {
    g = c;
    b = x;
  } else if (h >= 180 && h < 240) {
    g = x;
    b = c;
  } else if (h >= 240 && h < 300) {
    r = x;
    b = c;
  } else if (h >= 300 && h < 360) {
    r = c;
    b = x;
  }
  const toHex = (n: number) => {
    const v = Math.round((n + m) * 255).toString(16);
    return v.length === 1 ? `0${v}` : v;
  };
  return `#${toHex(r)}${toHex(g)}${toHex(b)}`;
}

export function shiftColor(hex: string, dh: number, ds: number, dl: number): string {
  const [h, s, l] = hexToHSL(hex);
  let nh = (h + dh) % 360;
  if (nh < 0) nh += 360;
  const ns = Math.max(0, Math.min(100, s + ds));
  const nl = Math.max(0, Math.min(100, l + dl));
  return hslToHex(nh, ns, nl);
}

export function hexToRgba(hex: string, alpha: number): string {
  const h = hex.replace("#", "");
  const r = parseInt(h.slice(0, 2), 16);
  const g = parseInt(h.slice(2, 4), 16);
  const b = parseInt(h.slice(4, 6), 16);
  return `rgba(${r}, ${g}, ${b}, ${alpha})`;
}

export function normalizeHex(value: string): string | null {
  const raw = value.replace("#", "").trim();
  if (!/^[0-9a-fA-F]{6}$/.test(raw)) return null;
  return `#${raw.toLowerCase()}`;
}

export function isHexColor(value: string): boolean {
  return /^#[0-9a-fA-F]{6}$/.test(value);
}

export function parseHexColor(value: string): Rgba | null {
  const raw = value.replace("#", "").trim();
  if (/^[0-9a-fA-F]{6}$/.test(raw)) {
    return {
      r: parseInt(raw.slice(0, 2), 16),
      g: parseInt(raw.slice(2, 4), 16),
      b: parseInt(raw.slice(4, 6), 16),
      a: 1,
    };
  }
  if (/^[0-9a-fA-F]{8}$/.test(raw)) {
    return {
      r: parseInt(raw.slice(0, 2), 16),
      g: parseInt(raw.slice(2, 4), 16),
      b: parseInt(raw.slice(4, 6), 16),
      a: parseInt(raw.slice(6, 8), 16) / 255,
    };
  }
  return null;
}

export function colorToHexAndAlpha(value: string): { hex: string; alphaPct: number } {
  const parsed = parseRgba(value) ?? parseHexColor(value);
  if (parsed) {
    return {
      hex: rgbToHex(parsed.r, parsed.g, parsed.b),
      alphaPct: Math.round(parsed.a * 100),
    };
  }
  return { hex: "#000000", alphaPct: 100 };
}

export function formatColorWithAlpha(hex: string, alphaPct: number): string {
  const normalized = normalizeHex(hex.replace("#", ""));
  const safeHex = normalized ?? hex;
  if (alphaPct >= 100) return safeHex;
  return hexToRgba(safeHex, Math.max(0, Math.min(100, alphaPct)) / 100);
}

export type Rgba = { r: number; g: number; b: number; a: number };

export function parseRgba(value: string): Rgba | null {
  const m = value
    .trim()
    .match(/^rgba?\(\s*([\d.]+)\s*,\s*([\d.]+)\s*,\s*([\d.]+)\s*(?:,\s*([\d.]+))?\s*\)$/i);
  if (!m) return null;
  return {
    r: Math.round(Number(m[1])),
    g: Math.round(Number(m[2])),
    b: Math.round(Number(m[3])),
    a: m[4] !== undefined ? Math.max(0, Math.min(1, Number(m[4]))) : 1,
  };
}

export function rgbToHex(r: number, g: number, b: number): string {
  const h = (n: number) => Math.round(n).toString(16).padStart(2, "0");
  return `#${h(r)}${h(g)}${h(b)}`;
}

/** Monaco editor chrome colors — #RRGGBBAA for reliable WebView parsing. */
export function toMonacoEditorColor(value: string, fallback: string): string {
  const raw = (value || fallback).trim();
  const rgba = parseRgba(raw);
  if (rgba) {
    const h = (n: number) => Math.round(n).toString(16).padStart(2, "0");
    const alpha = Math.round(rgba.a * 255)
      .toString(16)
      .padStart(2, "0");
    return `#${h(rgba.r)}${h(rgba.g)}${h(rgba.b)}${alpha}`;
  }
  if (/^#[0-9a-fA-F]{6}$/i.test(raw)) return raw.toLowerCase();
  if (/^#[0-9a-fA-F]{8}$/i.test(raw)) return raw.toLowerCase();
  const fb = parseRgba(fallback);
  if (fb) return toMonacoEditorColor(fallback, "#000000ff");
  return /^#[0-9a-fA-F]{6}$/i.test(fallback) ? `${fallback.toLowerCase()}ff` : "#2563eb54";
}
