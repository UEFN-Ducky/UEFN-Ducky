import { useMemo } from "react";
import type { ReactNode } from "react";
import { EnlargeableGraphic } from "../MediaLightbox";

// ```chart fences hold a JSON spec drawn here as SVG; ```svg fences are shown
// after sanitizeSvg. Colors are rich-c-<name> classes over Appearance variables.

const PALETTE = ["blue", "purple", "green", "amber", "yellow", "red"];
const W = 640;

export interface ChartSeries {
  name?: string;
  data: number[];
  color?: string;
}

export interface ChartStep {
  from: string;
  to: string;
  label?: string;
  dashed?: boolean;
}

export interface GraphNode {
  id: string;
  label?: string;
  group?: string;
  shape?: "box" | "pill" | "db" | "diamond";
  color?: string;
}

export interface GraphEdge {
  from: string;
  to: string;
  label?: string;
  dashed?: boolean;
}

export interface GraphGroup {
  id: string;
  label?: string;
  color?: string;
}

export interface TreeNode {
  label: string;
  color?: string;
  children?: TreeNode[];
}

export interface ChartSpec {
  type: "bar" | "line" | "area" | "pie" | "donut" | "sequence" | "graph" | "tree";
  title?: string;
  unit?: string;
  labels?: string[];
  series?: ChartSeries[];
  values?: number[];
  lanes?: string[];
  steps?: ChartStep[];
  direction?: "LR" | "TB";
  nodes?: GraphNode[];
  edges?: GraphEdge[];
  groups?: GraphGroup[];
  root?: TreeNode;
}

const TYPES = new Set(["bar", "line", "area", "pie", "donut", "sequence", "graph", "tree"]);

export function parseChartSpec(text: string): ChartSpec | null {
  let raw: unknown;
  try {
    raw = JSON.parse(text);
  } catch {
    return null; // still streaming, or not JSON: show as code
  }
  if (!raw || typeof raw !== "object") return null;
  const spec = raw as ChartSpec;
  if (!TYPES.has(spec.type)) return null;
  if (spec.type === "sequence") return Array.isArray(spec.lanes) && Array.isArray(spec.steps) ? spec : null;
  if (spec.type === "graph") {
    if (!Array.isArray(spec.nodes) || !spec.nodes.every((n) => typeof n?.id === "string")) return null;
    spec.edges = Array.isArray(spec.edges) ? spec.edges : [];
    return spec;
  }
  if (spec.type === "tree") return typeof spec.root?.label === "string" ? spec : null;
  if (!spec.series && Array.isArray(spec.values)) spec.series = [{ data: spec.values }];
  if (!Array.isArray(spec.series) || !spec.series.every((s) => Array.isArray(s?.data))) return null;
  return spec;
}

/** Round axis ticks (1/2/5 steps) covering [min, max], always including 0. */
export function niceTicks(min: number, max: number, count = 5): number[] {
  const lo = Math.min(0, min);
  const hi = Math.max(0, max);
  if (hi === lo) return [0, 1];
  const rough = (hi - lo) / count;
  const mag = 10 ** Math.floor(Math.log10(rough));
  const step = [1, 2, 5, 10].map((m) => m * mag).find((s) => s >= rough) ?? 10 * mag;
  const ticks: number[] = [];
  for (let v = Math.floor(lo / step) * step; v <= hi + step * 1e-9; v += step) {
    ticks.push(Number(v.toFixed(10)));
  }
  if (ticks[ticks.length - 1] < hi) ticks.push(ticks[ticks.length - 1] + step);
  return ticks;
}

function tone(name: string | undefined, i: number): string {
  return `rich-c-${name && PALETTE.includes(name) ? name : PALETTE[i % PALETTE.length]}`;
}

function fmt(v: number, unit?: string): string {
  const abs = Math.abs(v);
  const s = abs >= 1e6 ? `${+(v / 1e6).toFixed(1)}M` : abs >= 1e4 ? `${+(v / 1e3).toFixed(1)}k` : `${+v.toFixed(2)}`;
  return unit ? `${s} ${unit}` : s;
}

function CartesianChart({ spec }: { spec: ChartSpec }) {
  const series = spec.series ?? [];
  const n = Math.max(...series.map((s) => s.data.length), 0);
  const labels = spec.labels ?? Array.from({ length: n }, (_, i) => String(i + 1));
  const all = series.flatMap((s) => s.data);
  const ticks = niceTicks(Math.min(...all), Math.max(...all));
  const H = 260, L = 48, R = 12, T = 12, B = 30;
  const lo = ticks[0], hi = ticks[ticks.length - 1];
  const y = (v: number) => T + (H - T - B) * (1 - (v - lo) / (hi - lo));
  const band = (W - L - R) / Math.max(n, 1);
  const cx = (i: number) => L + band * (i + 0.5);
  const every = Math.ceil(labels.length / 12); // ponytail: thin x labels past ~12, no rotation
  const tip = (s: ChartSeries, i: number, v: number) =>
    `${s.name ? `${s.name} · ` : ""}${labels[i] ?? i}: ${fmt(v, spec.unit)}`;

  const marks: ReactNode[] = [];
  if (spec.type === "bar") {
    const bw = (band * 0.72) / Math.max(series.length, 1);
    series.forEach((s, si) => s.data.forEach((v, i) => {
      marks.push(
        <rect key={`${si}-${i}`} className={`rich-chart-mark rich-chart-bar ${tone(s.color, si)}`}
          x={L + band * i + band * 0.14 + bw * si} y={y(Math.max(v, 0))}
          width={Math.max(bw - 2, 1)} height={Math.max(Math.abs(y(v) - y(0)), 1)} rx={3}>
          <title>{tip(s, i, v)}</title>
        </rect>,
      );
    }));
  } else {
    series.forEach((s, si) => {
      const c = tone(s.color, si);
      const pts = s.data.map((v, i) => `${cx(i)},${y(v)}`);
      if (spec.type === "area" && pts.length) {
        marks.push(<path key={`a${si}`} className={`rich-chart-area ${c}`}
          d={`M${cx(0)},${y(0)} L${pts.join(" L")} L${cx(s.data.length - 1)},${y(0)} Z`} />);
      }
      marks.push(<polyline key={`l${si}`} className={`rich-chart-line ${c}`} points={pts.join(" ")} />);
      s.data.forEach((v, i) => marks.push(
        <circle key={`p${si}-${i}`} className={`rich-chart-mark rich-chart-dot ${c}`} cx={cx(i)} cy={y(v)} r={3.5}>
          <title>{tip(s, i, v)}</title>
        </circle>,
      ));
    });
  }

  return (
    <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label={spec.title ?? `${spec.type} chart`}>
      {ticks.map((t) => (
        <g key={t}>
          <line x1={L} x2={W - R} y1={y(t)} y2={y(t)} className={t === 0 ? "rich-chart-axis" : "rich-chart-grid"} />
          <text x={L - 8} y={y(t) + 4} textAnchor="end" className="rich-chart-tick">{fmt(t)}</text>
        </g>
      ))}
      {labels.map((lab, i) => (i % every === 0 ? (
        <text key={i} x={cx(i)} y={H - 10} textAnchor="middle" className="rich-chart-tick">{lab}</text>
      ) : null))}
      {marks}
    </svg>
  );
}

function slice(cx: number, cy: number, r: number, hole: number, a0: number, a1: number): string {
  const p = (rad: number, a: number) => `${cx + rad * Math.sin(a)},${cy - rad * Math.cos(a)}`;
  const big = a1 - a0 > Math.PI ? 1 : 0;
  if (a1 - a0 >= Math.PI * 2 - 1e-6) a1 = a0 + Math.PI * 2 - 1e-4; // a full circle needs a hair of gap
  const outer = `M${p(r, a0)} A${r},${r} 0 ${big} 1 ${p(r, a1)}`;
  return hole > 0
    ? `${outer} L${p(hole, a1)} A${hole},${hole} 0 ${big} 0 ${p(hole, a0)} Z`
    : `${outer} L${cx},${cy} Z`;
}

function PieChart({ spec }: { spec: ChartSpec }) {
  const data = (spec.series?.[0]?.data ?? []).map((v) => Math.max(v, 0));
  const total = data.reduce((a, b) => a + b, 0) || 1;
  const labels = spec.labels ?? [];
  const H = 240, cx = W / 2, cy = H / 2, r = 104;
  const hole = spec.type === "donut" ? r * 0.58 : 0;
  let a = 0;
  return (
    <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label={spec.title ?? "pie chart"}>
      {data.map((v, i) => {
        const a0 = a;
        a += (v / total) * Math.PI * 2;
        return (
          <path key={i} className={`rich-chart-mark rich-chart-slice ${tone(undefined, i)}`} d={slice(cx, cy, r, hole, a0, a)}>
            <title>{`${labels[i] ?? i}: ${fmt(v, spec.unit)} (${Math.round((v / total) * 100)}%)`}</title>
          </path>
        );
      })}
      {hole ? <text x={cx} y={cy + 8} textAnchor="middle" className="rich-chart-total">{fmt(total, spec.unit)}</text> : null}
    </svg>
  );
}

function SequenceChart({ spec }: { spec: ChartSpec }) {
  const lanes = spec.lanes ?? [];
  const steps = spec.steps ?? [];
  const col = W / Math.max(lanes.length, 1);
  const x = (name: string) => col * (Math.max(lanes.indexOf(name), 0) + 0.5);
  const top = 46, row = 40;
  const H = top + steps.length * row + 20;
  return (
    <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label={spec.title ?? "sequence diagram"}>
      {lanes.map((lane, i) => (
        <g key={lane} className={tone(undefined, i)}>
          <line x1={x(lane)} x2={x(lane)} y1={34} y2={H - 6} className="rich-chart-lifeline" />
          <rect x={x(lane) - col * 0.42} y={4} width={col * 0.84} height={30} rx={8} className="rich-chart-lane" />
          <text x={x(lane)} y={24} textAnchor="middle" className="rich-chart-lane-label">{lane}</text>
        </g>
      ))}
      {steps.map((s, i) => {
        const y = top + i * row + row * 0.6;
        const x1 = x(s.from), x2 = x(s.to);
        const c = tone(undefined, Math.max(lanes.indexOf(s.from), 0));
        if (x1 === x2) {
          return (
            <g key={i} className={c}>
              <path className="rich-chart-msg" d={`M${x1},${y - 8} h28 v14 h-24`} />
              <polygon className="rich-chart-head" points={`${x1},${y + 6} ${x1 + 8},${y + 2} ${x1 + 8},${y + 10}`} />
              <text x={x1 + 34} y={y + 3} className="rich-chart-step">{s.label}</text>
            </g>
          );
        }
        const dir = x2 > x1 ? 1 : -1;
        return (
          <g key={i} className={c}>
            <line className="rich-chart-msg" x1={x1} x2={x2 - dir * 8} y1={y} y2={y} strokeDasharray={s.dashed ? "5 4" : undefined} />
            <polygon className="rich-chart-head" points={`${x2},${y} ${x2 - dir * 9},${y - 5} ${x2 - dir * 9},${y + 5}`} />
            <text x={(x1 + x2) / 2} y={y - 7} textAnchor="middle" className="rich-chart-step">{s.label}</text>
          </g>
        );
      })}
    </svg>
  );
}

const NODE_H = 38;
const nodeW = (label: string) => Math.max(84, label.length * 7.2 + 28);

/** Layer per node by longest path from the sources; back edges are ignored so cycles can't loop. */
export function graphLayers(nodes: GraphNode[], edges: GraphEdge[]): Map<string, number> {
  const out = new Map<string, string[]>(nodes.map((n) => [n.id, []]));
  for (const e of edges) if (out.has(e.to) && e.from !== e.to) out.get(e.from)?.push(e.to);
  const state = new Map<string, 1 | 2>();
  const forward = new Map<string, string[]>();
  const post: string[] = [];
  const visit = (id: string) => {
    state.set(id, 1);
    forward.set(id, []);
    for (const t of out.get(id) ?? []) {
      if (state.get(t) === 1) continue; // back edge
      forward.get(id)?.push(t);
      if (!state.has(t)) visit(t);
    }
    state.set(id, 2);
    post.push(id);
  };
  const hasIn = new Set(edges.map((e) => e.to));
  [...nodes.filter((n) => !hasIn.has(n.id)), ...nodes].forEach((n) => { if (!state.has(n.id)) visit(n.id); });
  const layer = new Map<string, number>(nodes.map((n) => [n.id, 0]));
  for (const id of post.reverse()) {
    for (const t of forward.get(id) ?? []) layer.set(t, Math.max(layer.get(t) ?? 0, (layer.get(id) ?? 0) + 1));
  }
  return layer;
}

interface Box { x: number; y: number; w: number; h: number }

function nodeShape(shape: GraphNode["shape"], b: Box): ReactNode {
  const x0 = b.x - b.w / 2, y0 = b.y - b.h / 2, x1 = b.x + b.w / 2, y1 = b.y + b.h / 2;
  if (shape === "diamond") {
    return <polygon className="rich-chart-node" points={`${b.x},${y0 - 6} ${x1 + 10},${b.y} ${b.x},${y1 + 6} ${x0 - 10},${b.y}`} />;
  }
  if (shape === "db") {
    const e = 6, rx = b.w / 2;
    return (
      <>
        <path className="rich-chart-node" d={`M${x0},${y0 + e} A${rx},${e} 0 0 1 ${x1},${y0 + e} V${y1 - e} A${rx},${e} 0 0 1 ${x0},${y1 - e} Z`} />
        <path className="rich-chart-node-rim" d={`M${x0},${y0 + e} A${rx},${e} 0 0 0 ${x1},${y0 + e}`} />
      </>
    );
  }
  return <rect className="rich-chart-node" x={x0} y={y0} width={b.w} height={b.h} rx={shape === "pill" ? b.h / 2 : 8} />;
}

/** Bezier edge a→b. Forward edges leave the far side; back edges loop around the outside. */
function route(a: Box, b: Box, aTip: number, bTip: number, lr: boolean, back: boolean) {
  let sx: number, sy: number, ex: number, ey: number, c1x: number, c1y: number, c2x: number, c2y: number;
  if (back) {
    [sx, sy, ex, ey] = lr ? [a.x, a.y + a.h / 2 + aTip, b.x, b.y + b.h / 2 + bTip]
      : [a.x + a.w / 2 + aTip, a.y, b.x + b.w / 2 + bTip, b.y];
    const bulge = 40 + Math.abs(lr ? ex - sx : ey - sy) * 0.12;
    [c1x, c1y, c2x, c2y] = lr ? [sx, sy + bulge, ex, ey + bulge] : [sx + bulge, sy, ex + bulge, ey];
  } else {
    [sx, sy, ex, ey] = lr ? [a.x + a.w / 2 + aTip, a.y, b.x - b.w / 2 - bTip, b.y]
      : [a.x, a.y + a.h / 2 + aTip, b.x, b.y - b.h / 2 - bTip];
    const d = Math.max(28, Math.abs(lr ? ex - sx : ey - sy) / 2);
    [c1x, c1y, c2x, c2y] = lr ? [sx + d, sy, ex - d, ey] : [sx, sy + d, ex, ey - d];
  }
  const ang = Math.atan2(ey - c2y, ex - c2x);
  const head = [ang + 2.7, ang - 2.7].map((t) => `${ex + 9 * Math.cos(t)},${ey + 9 * Math.sin(t)}`).join(" ");
  return {
    path: `M${sx},${sy} C${c1x},${c1y} ${c2x},${c2y} ${ex},${ey}`,
    head: `${ex},${ey} ${head}`,
    mid: [(sx + 3 * c1x + 3 * c2x + ex) / 8, (sy + 3 * c1y + 3 * c2y + ey) / 8],
    nearEnd: lr ? [ex - 42, ey] : [ex, ey - 32], // in the gap just before the target
    hull: { x: Math.min(sx, c1x, c2x, ex), y: Math.min(sy, c1y, c2y, ey),
      w: Math.max(sx, c1x, c2x, ex) - Math.min(sx, c1x, c2x, ex), h: Math.max(sy, c1y, c2y, ey) - Math.min(sy, c1y, c2y, ey) },
  };
}

function GraphChart({ spec }: { spec: ChartSpec }) {
  const nodes = spec.nodes ?? [];
  const edges = (spec.edges ?? []).filter((e) => nodes.some((n) => n.id === e.from) && nodes.some((n) => n.id === e.to));
  const groups = spec.groups ?? [];
  const lr = spec.direction !== "TB";
  const layer = graphLayers(nodes, edges);
  if (groups.length) {
    // Each group (and each loose node) gets its own run of layers so group boxes never overlap.
    const band = (n: GraphNode) => (n.group && groups.some((g) => g.id === n.group) ? n.group : `\0${n.id}`);
    const bands = new Map<string, GraphNode[]>();
    nodes.forEach((n) => bands.set(band(n), [...(bands.get(band(n)) ?? []), n]));
    const avg = (ms: GraphNode[]) => ms.reduce((s, n) => s + (layer.get(n.id) ?? 0), 0) / ms.length;
    let offset = 0;
    for (const members of [...bands.values()].sort((a, b) => avg(a) - avg(b))) {
      const ids = new Set(members.map((n) => n.id));
      const local = graphLayers(members, edges.filter((e) => ids.has(e.from) && ids.has(e.to)));
      const depth = Math.max(...members.map((n) => local.get(n.id) ?? 0));
      members.forEach((n) => layer.set(n.id, offset + (local.get(n.id) ?? 0)));
      offset += depth + 1;
    }
  }
  const groupOrder = (n: GraphNode) => (n.group ? groups.findIndex((g) => g.id === n.group) + 1 || groups.length + 1 : 0);

  // ponytail: one barycenter pass, group-first ordering; a real crossing minimizer if big graphs tangle
  const layers: GraphNode[][] = [];
  nodes.forEach((n) => (layers[layer.get(n.id) ?? 0] ??= []).push(n));
  const rank = new Map<string, number>();
  layers.forEach((col, k) => {
    const bary = (n: GraphNode) => {
      const preds = edges.filter((e) => e.to === n.id && (layer.get(e.from) ?? 0) < k).map((e) => rank.get(e.from) ?? 0);
      return preds.length ? preds.reduce((a, b) => a + b, 0) / preds.length : nodes.indexOf(n);
    };
    col.sort((a, b) => groupOrder(a) - groupOrder(b) || bary(a) - bary(b));
    col.forEach((n, i) => rank.set(n.id, i));
  });

  const box = new Map<string, Box>();
  const along = layers.map((col) => Math.max(...col.map((n) => (lr ? nodeW(n.label ?? n.id) : NODE_H))));
  const span = (col: GraphNode[]) => col.reduce((sum, n, i) =>
    sum + (lr ? NODE_H : nodeW(n.label ?? n.id)) + (i ? (groupOrder(n) !== groupOrder(col[i - 1]) ? 44 : 20) : 0), 0);
  const widest = Math.max(...layers.map(span));
  let pos = 0;
  layers.forEach((col, k) => {
    let across = (widest - span(col)) / 2;
    col.forEach((n, i) => {
      const w = nodeW(n.label ?? n.id);
      const size = lr ? NODE_H : w;
      if (i) across += groupOrder(n) !== groupOrder(col[i - 1]) ? 44 : 20;
      const c = across + size / 2, m = pos + along[k] / 2;
      box.set(n.id, { x: lr ? m : c, y: lr ? c : m, w, h: NODE_H });
      across += size;
    });
    pos += along[k] + (lr ? 90 : 70);
  });

  const groupBoxes = groups.flatMap((g, gi) => {
    const members = nodes.filter((n) => n.group === g.id).map((n) => box.get(n.id) as Box);
    if (!members.length) return [];
    const x0 = Math.min(...members.map((b) => b.x - b.w / 2)) - 16, x1 = Math.max(...members.map((b) => b.x + b.w / 2)) + 16;
    const y0 = Math.min(...members.map((b) => b.y - b.h / 2)) - 30, y1 = Math.max(...members.map((b) => b.y + b.h / 2)) + 14;
    return [{ g, gi, x: x0, y: y0, w: x1 - x0, h: y1 - y0 }];
  });
  const shapeOf = new Map(nodes.map((n) => [n.id, n.shape]));
  const tip = (id: string) => (shapeOf.get(id) === "diamond" ? (lr ? 10 : 6) : 0);
  const routes = edges.map((e) => route(box.get(e.from) as Box, box.get(e.to) as Box, tip(e.from), tip(e.to), lr,
    (layer.get(e.to) ?? 0) <= (layer.get(e.from) ?? 0)));
  const all = [...box.values()].map((b) => ({ x: b.x - b.w / 2 - 12, y: b.y - b.h / 2 - 8, w: b.w + 24, h: b.h + 16 }));
  const bounds = [...all, ...groupBoxes, ...routes.map((r) => r.hull)];
  const minX = Math.min(...bounds.map((b) => b.x)) - 8, minY = Math.min(...bounds.map((b) => b.y)) - 8;
  const W2 = Math.max(...bounds.map((b) => b.x + b.w)) + 8 - minX, H2 = Math.max(...bounds.map((b) => b.y + b.h)) + 8 - minY;

  const toneOf = (n: GraphNode) => {
    const gi = groups.findIndex((g) => g.id === n.group);
    return gi >= 0 ? tone(n.color ?? groups[gi].color, gi) : tone(n.color, layer.get(n.id) ?? 0);
  };

  return (
    <svg className="rich-chart-fit" viewBox={`${minX} ${minY} ${W2} ${H2}`} width={W2} role="img" aria-label={spec.title ?? "diagram"}>
      {groupBoxes.map(({ g, gi, x, y, w, h }) => (
        <g key={g.id} className={tone(g.color, gi)}>
          <rect className="rich-chart-group" x={x} y={y} width={w} height={h} rx={12} />
          <text className="rich-chart-group-label" x={x + 12} y={y + 18}>{g.label ?? g.id}</text>
        </g>
      ))}
      {edges.map((e, i) => (
        <g key={i}>
          <path className="rich-chart-edge" d={routes[i].path} strokeDasharray={e.dashed ? "5 4" : undefined} />
          <polygon className="rich-chart-edge-head" points={routes[i].head} />
        </g>
      ))}
      {nodes.map((n) => {
        const b = box.get(n.id) as Box;
        return (
          <g key={n.id} className={`rich-chart-mark ${toneOf(n)}`}>
            {nodeShape(n.shape, b)}
            <text className="rich-chart-node-label" x={b.x} y={b.y + 4.5} textAnchor="middle">{n.label ?? n.id}</text>
          </g>
        );
      })}
      {edges.map((e, i) => {
        if (!e.label) return null; // labels last so a crossing node never hides them
        // An edge that skips layers would put its midpoint on a node in between.
        const skips = (layer.get(e.to) ?? 0) - (layer.get(e.from) ?? 0) > 1;
        const [mx, my] = skips ? routes[i].nearEnd : routes[i].mid, lw = e.label.length * 6.2 + 10;
        return (
          <g key={`l${i}`}>
            <rect className="rich-chart-edge-bg" x={mx - lw / 2} y={my - 9} width={lw} height={18} rx={4} />
            <text className="rich-chart-edge-label" x={mx} y={my + 4} textAnchor="middle">{e.label}</text>
          </g>
        );
      })}
    </svg>
  );
}

function TreeChart({ spec }: { spec: ChartSpec }) {
  const placed: { node: TreeNode; x: number; y: number; depth: number }[] = [];
  const links: [number, number][] = [];
  const widest = (n: TreeNode): number => Math.max(nodeW(n.label), ...(n.children ?? []).map(widest));
  const slot = (spec.root ? widest(spec.root) : 84) + 16;
  const levelH = NODE_H + 46;
  let leaf = 0;
  const place = (node: TreeNode, depth: number): number => {
    const kids = (node.children ?? []).map((k) => place(k, depth + 1));
    const x = kids.length ? (placed[kids[0]].x + placed[kids[kids.length - 1]].x) / 2 : slot * (leaf++ + 0.5);
    placed.push({ node, x, y: depth * levelH + NODE_H / 2 + 4, depth });
    const me = placed.length - 1;
    kids.forEach((k) => links.push([me, k]));
    return me;
  };
  if (spec.root) place(spec.root, 0);
  const W2 = Math.max(leaf, 1) * slot, H2 = Math.max(...placed.map((p) => p.y)) + NODE_H / 2 + 6;
  return (
    <svg className="rich-chart-fit" viewBox={`0 0 ${W2} ${H2}`} width={W2} role="img" aria-label={spec.title ?? "tree"}>
      {links.map(([p, c]) => {
        const a = placed[p], b = placed[c], mid = (a.y + b.y) / 2;
        return <path key={c} className="rich-chart-edge" d={`M${a.x},${a.y + NODE_H / 2} V${mid - 8} Q${a.x},${mid} ${a.x + Math.sign(b.x - a.x) * 8},${mid} H${b.x - Math.sign(b.x - a.x) * 8} Q${b.x},${mid} ${b.x},${mid + 8} V${b.y - NODE_H / 2}`} />;
      })}
      {placed.map((p, i) => {
        const w = nodeW(p.node.label);
        return (
          <g key={i} className={`rich-chart-mark ${tone(p.node.color, p.depth)}`}>
            <rect className="rich-chart-node" x={p.x - w / 2} y={p.y - NODE_H / 2} width={w} height={NODE_H} rx={8} />
            <text className="rich-chart-node-label" x={p.x} y={p.y + 4.5} textAnchor="middle">{p.node.label}</text>
          </g>
        );
      })}
    </svg>
  );
}

export function RichChart({ spec }: { spec: ChartSpec }) {
  const legend = spec.type === "pie" || spec.type === "donut"
    ? (spec.labels ?? []).map((name, i) => ({ name, tone: tone(undefined, i) }))
    : spec.type === "sequence" || spec.type === "graph" || spec.type === "tree" ? []
    : (spec.series ?? []).flatMap((s, i) => (s.name ? [{ name: s.name, tone: tone(s.color, i) }] : []));
  return (
    <figure className="rich-chart">
      {spec.title ? <figcaption className="rich-chart-title">{spec.title}</figcaption> : null}
      {spec.type === "sequence" ? <SequenceChart spec={spec} />
        : spec.type === "graph" ? <GraphChart spec={spec} />
        : spec.type === "tree" ? <TreeChart spec={spec} />
        : spec.type === "pie" || spec.type === "donut" ? <PieChart spec={spec} />
        : <CartesianChart spec={spec} />}
      {legend.length ? (
        <div className="rich-chart-legend">
          {legend.map((l) => (
            <span key={l.name} className={`rich-chart-legend-item ${l.tone}`}><i />{l.name}</span>
          ))}
        </div>
      ) : null}
    </figure>
  );
}

const SVG_TAGS = new Set([
  "svg", "g", "path", "rect", "circle", "ellipse", "line", "polyline", "polygon", "text", "tspan", "textpath",
  "defs", "lineargradient", "radialgradient", "stop", "marker", "title", "desc", "clippath", "mask", "pattern",
  "use", "symbol", "filter", "fegaussianblur", "feoffset", "feblend", "fedropshadow", "femerge", "femergenode",
  "fecolormatrix", "feflood", "fecomposite",
]);

/** Agent-written SVG → safe markup: allowlisted tags, no on* handlers, no outside links. */
export function sanitizeSvg(text: string): string | null {
  const doc = new DOMParser().parseFromString(text.trim(), "image/svg+xml");
  const root = doc.documentElement;
  if (!root || root.nodeName.toLowerCase() !== "svg" || doc.getElementsByTagName("parsererror").length) return null;
  const walk = (el: Element) => {
    for (const child of Array.from(el.children)) {
      if (!SVG_TAGS.has(child.nodeName.toLowerCase())) child.remove();
      else walk(child);
    }
    for (const attr of Array.from(el.attributes)) {
      const name = attr.name.toLowerCase();
      const value = attr.value.trim();
      const outside = /url\(\s*['"]?(?!#)/i.test(value) || /javascript:/i.test(value);
      if (name.startsWith("on") || outside || ((name === "href" || name === "xlink:href") && !value.startsWith("#"))) {
        el.removeAttribute(attr.name);
      }
    }
  };
  walk(root);
  if (!root.getAttribute("viewBox")) {
    const w = parseFloat(root.getAttribute("width") ?? ""), h = parseFloat(root.getAttribute("height") ?? "");
    if (w > 0 && h > 0) root.setAttribute("viewBox", `0 0 ${w} ${h}`);
  }
  root.removeAttribute("width");
  root.removeAttribute("height");
  return new XMLSerializer().serializeToString(root);
}

/** Chart/SVG for a fence, or null when the fence is plain code (or not finished streaming). */
export function useRichGraphic(text: string, language?: string): ReactNode | null {
  return useMemo(() => {
    const lang = language?.toLowerCase();
    // Every chart and picture in the chat opens big from the button in its corner.
    if (lang === "chart") {
      const spec = parseChartSpec(text);
      return spec ? <EnlargeableGraphic name={spec.title || "Chart"}><RichChart spec={spec} /></EnlargeableGraphic> : null;
    }
    if (lang === "svg") {
      const markup = sanitizeSvg(text);
      // Sanitized above: allowlisted tags only, no handlers or outside URLs.
      return markup ? (
        <EnlargeableGraphic name="Picture">
          <figure className="rich-chart rich-chart--svg" dangerouslySetInnerHTML={{ __html: markup }} />
        </EnlargeableGraphic>
      ) : null;
    }
    return null;
  }, [text, language]);
}
