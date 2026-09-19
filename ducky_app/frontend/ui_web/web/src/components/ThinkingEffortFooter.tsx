import { useId, useRef, useState, type CSSProperties } from "react";

import {
  effortInMenu,
  footerThinkingMenu,
  formatEffortReadout,
  menuLevel,
  thinkingSliderEnabled,
  type ThinkingMenu,
} from "./thinkingMenu";

interface ThinkingEffortFooterProps {
  menu: ThinkingMenu | null | undefined;
  modelName: string;
  effort: string;
  onChange: (id: string) => void;
}

/** 0 = Faster/green, 1 = Smarter/red. Mixes theme tokens — no hex. */
export function effortSliderColor(heat: number): string {
  const t = Math.max(0, Math.min(1, heat));
  if (t <= 0.5) return `color-mix(in srgb, var(--amber) ${Math.round(t * 200)}%, var(--green))`;
  return `color-mix(in srgb, var(--red) ${Math.round((t - 0.5) * 200)}%, var(--amber))`;
}

/** Cubic fire: tiny until red. Matches the example's scale/opacity curve. */
export function effortFire(heat: number): { scale: number; opacity: number } {
  const n = Math.max(0, Math.min(1, heat));
  if (n <= 0.03) return { scale: 0, opacity: 0 };
  return { scale: Math.max(0.05, n ** 3 * 0.55), opacity: Math.min(1, n * 1.5) };
}

function EffortFire({ uid }: { uid: string }) {
  const back = `${uid}-back`;
  const mid = `${uid}-mid`;
  const front = `${uid}-front`;
  const pBack = `${uid}-pback`;
  const pMid = `${uid}-pmid`;
  const pFront = `${uid}-pfront`;
  return (
    <svg width="100%" height="100%" preserveAspectRatio="none" aria-hidden="true">
      <defs>
        <linearGradient id={back} x1="0%" y1="100%" x2="0%" y2="0%">
          <stop offset="0%" stopColor="currentColor" stopOpacity="0.9" />
          <stop offset="100%" stopColor="currentColor" stopOpacity="0" />
        </linearGradient>
        <linearGradient id={mid} x1="0%" y1="100%" x2="0%" y2="0%">
          <stop offset="0%" stopColor="currentColor" stopOpacity="1" />
          <stop offset="85%" stopColor="currentColor" stopOpacity="0" />
        </linearGradient>
        <linearGradient id={front} x1="0%" y1="100%" x2="0%" y2="0%">
          <stop offset="0%" stopColor="currentColor" stopOpacity="1" />
          <stop offset="60%" stopColor="currentColor" stopOpacity="0" />
        </linearGradient>
        <pattern id={pBack} width="80" height="80" patternUnits="userSpaceOnUse">
          <g>
            <animateTransform attributeName="transform" type="translate" from="0 0" to="-80 0" dur="0.8s" repeatCount="indefinite" />
            <path d="M 0 80 Q 20 15 40 0 Q 60 40 80 80 Z" fill={`url(#${back})`} />
            <path d="M 80 80 Q 100 15 120 0 Q 140 40 160 80 Z" fill={`url(#${back})`} />
          </g>
        </pattern>
        <pattern id={pMid} width="50" height="80" patternUnits="userSpaceOnUse">
          <g>
            <animateTransform attributeName="transform" type="translate" from="0 0" to="-50 0" dur="0.5s" repeatCount="indefinite" />
            <path d="M 0 80 Q 12.5 30 25 10 Q 37.5 50 50 80 Z" fill={`url(#${mid})`} />
            <path d="M 50 80 Q 62.5 30 75 10 Q 87.5 50 100 80 Z" fill={`url(#${mid})`} />
          </g>
        </pattern>
        <pattern id={pFront} width="30" height="80" patternUnits="userSpaceOnUse">
          <g>
            <animateTransform attributeName="transform" type="translate" from="0 0" to="-30 0" dur="0.3s" repeatCount="indefinite" />
            <path d="M 0 80 Q 7.5 45 15 25 Q 22.5 55 30 80 Z" fill={`url(#${front})`} />
            <path d="M 30 80 Q 37.5 45 45 25 Q 52.5 55 60 80 Z" fill={`url(#${front})`} />
          </g>
        </pattern>
      </defs>
      <rect width="100%" height="100%" fill={`url(#${pBack})`} />
      <rect className="model-selector-effort-fire-screen" width="100%" height="100%" fill={`url(#${pMid})`} />
      <rect className="model-selector-effort-fire-screen" width="100%" height="100%" fill={`url(#${pFront})`} />
    </svg>
  );
}

/** Pinned Faster/Smarter slider. Always visible; disabled when the gateway sent no menu. */
export function ThinkingEffortFooter({ menu, modelName, effort, onChange }: ThinkingEffortFooterProps) {
  const resolved = footerThinkingMenu(menu);
  const levels = resolved.levels;
  const enabled = thinkingSliderEnabled(menu);
  const id = effortInMenu(resolved, enabled ? effort : "off");
  const index = Math.max(
    0,
    levels.findIndex((l) => (l.id || "").toLowerCase() === id),
  );
  const level = menuLevel(resolved, id);
  const lo = resolved.lo || "Faster";
  const hi = resolved.hi || "Smarter";
  const last = Math.max(0, levels.length - 1);
  const snapped = last === 0 ? 0 : index / last;
  const [dragHeat, setDragHeat] = useState<number | null>(null);
  const heat = enabled ? (dragHeat ?? snapped) : 0;
  const fire = effortFire(heat);
  const trackRef = useRef<HTMLDivElement>(null);
  const uid = useId().replace(/:/g, "");

  const heatFromClientX = (clientX: number) => {
    const el = trackRef.current;
    if (!el) return 0;
    const r = el.getBoundingClientRect();
    if (r.width <= 0) return 0;
    return Math.max(0, Math.min(1, (clientX - r.left) / r.width));
  };

  const commitHeat = (next: number) => {
    const idx = last === 0 ? 0 : Math.round(next * last);
    const row = levels[idx];
    if (row?.id) onChange(row.id);
  };

  return (
    <div
      className={`model-selector-effort${enabled ? "" : " is-disabled"}`}
      style={
        {
          ["--effort-heat"]: String(heat),
          ["--slider-color"]: effortSliderColor(heat),
          ["--fire-scale"]: String(fire.scale),
          ["--fire-opacity"]: String(fire.opacity),
        } as CSSProperties
      }
      onClick={(e) => e.stopPropagation()}
    >
      <div className="model-selector-effort-readout">{formatEffortReadout(modelName, level)}</div>
      <div
        className="model-selector-effort-slider"
        onPointerDown={(e) => {
          if (!enabled) return;
          e.currentTarget.setPointerCapture(e.pointerId);
          setDragHeat(heatFromClientX(e.clientX));
        }}
        onPointerMove={(e) => {
          if (!enabled || dragHeat === null) return;
          setDragHeat(heatFromClientX(e.clientX));
        }}
        onPointerUp={(e) => {
          if (!enabled || dragHeat === null) return;
          const next = heatFromClientX(e.clientX);
          setDragHeat(null);
          commitHeat(next);
        }}
        onPointerCancel={() => setDragHeat(null)}
      >
        <div className="model-selector-effort-fire">
          <div className="model-selector-effort-fire-inner">
            <EffortFire uid={uid} />
          </div>
        </div>
        <div className="model-selector-effort-rail" ref={trackRef} aria-hidden="true">
          <span className="model-selector-effort-dots" />
          <span className="model-selector-effort-fill">
            <span className="model-selector-effort-fill-dots" />
          </span>
        </div>
        <span className="model-selector-effort-thumb" />
        <input
          type="range"
          className="model-selector-effort-range"
          min={0}
          max={last}
          step={1}
          value={index}
          disabled={!enabled}
          aria-label="Thinking effort"
          onChange={(e) => {
            if (!enabled) return;
            const next = levels[Number(e.target.value)];
            if (next?.id) onChange(next.id);
          }}
        />
      </div>
      <div className="model-selector-effort-labels">
        <span>{lo}</span>
        <span>{hi}</span>
      </div>
    </div>
  );
}
