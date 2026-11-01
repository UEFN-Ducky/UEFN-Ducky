/**
 * The Welcome page's moving picture: Ducky in the middle sends work out to what the duckies do
 * (Verse, levels, art and 3D, play tests) while the four duckies waddle along the ground.
 * Hover a ducky and the scene holds still; click it and it says what it does, and its job lights up.
 * SVG SMIL like the Store's Team Storage slides, so it costs no JavaScript per frame.
 */
import { useEffect, useRef, useState } from "react";

const DUCKS = [
  { id: "wizard", img: "./duckies/wizard.png", name: "Wizard", job: "Verse", says: "I write and fix your Verse." },
  { id: "cowboy", img: "./duckies/cowboy.png", name: "Cowboy", job: "Levels", says: "I build and dress your levels." },
  { id: "artist", img: "./duckies/Artist.png", name: "Artist", job: "Art & 3D", says: "I make materials, VFX and 3D models." },
  { id: "hacker", img: "./duckies/hacker.png", name: "Hacker", job: "Play test", says: "I play your island and find the bugs." },
] as const;

const W = 640;
const HUB = { x: 320, y: 92 };
// The four jobs, two on each side of Ducky.
const JOBS = [
  { x: 92, y: 52 },
  { x: 92, y: 132 },
  { x: 548, y: 52 },
  { x: 548, y: 132 },
];
const GROUND = 214;
const WALK_S = 16;

function route(i: number): string {
  const j = JOBS[i];
  const side = j.x < HUB.x ? -1 : 1;
  const from = HUB.x + side * 44;
  const to = j.x - side * 64;
  const mid = (from + to) / 2;
  return `M${from} ${HUB.y} C${mid} ${HUB.y} ${mid} ${j.y} ${to} ${j.y}`;
}

export function SetupWelcomeScene() {
  const svg = useRef<SVGSVGElement>(null);
  const [picked, setPicked] = useState<number | null>(null);

  // Reduced motion: the picture holds still (SMIL ignores CSS, so pause it here).
  useEffect(() => {
    const el = svg.current;
    const mq = typeof window.matchMedia === "function" ? window.matchMedia("(prefers-reduced-motion: reduce)") : null;
    if (mq?.matches && typeof el?.pauseAnimations === "function") el.pauseAnimations();
  }, []);
  const hold = (on: boolean) => {
    const el = svg.current;
    if (!el || typeof el.pauseAnimations !== "function") return;
    const still = typeof window.matchMedia === "function" && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    if (on || still) el.pauseAnimations();
    else el.unpauseAnimations();
  };

  const said = picked === null ? null : DUCKS[picked];

  return (
    <div className="setup-scene">
      <svg
        ref={svg}
        className="setup-scene-svg"
        viewBox={`0 0 ${W} 240`}
        role="img"
        aria-label="Ducky in the middle sends work to four duckies: Verse, levels, art and 3D, and play tests. The duckies walk along the bottom; click one to hear what it does."
      >
        <defs>
          <clipPath id="setup-scene-hub">
            <circle cx={HUB.x} cy={HUB.y} r="38" />
          </clipPath>
        </defs>

        <g className="setup-scene-routes" aria-hidden>
          {JOBS.map((_, i) => (
            <path key={i} id={`setup-route-${i}`} d={route(i)} />
          ))}
        </g>
        <g className="setup-scene-flow" aria-hidden>
          {JOBS.map((_, i) => (
            <circle key={i} r="4">
              <animateMotion
                dur="2.4s"
                repeatCount="indefinite"
                begin={`${i * 0.6}s`}
                keyPoints="0;1"
                keyTimes="0;1"
                calcMode="linear"
              >
                <mpath href={`#setup-route-${i}`} />
              </animateMotion>
            </circle>
          ))}
        </g>

        {JOBS.map((j, i) => (
          <g key={i} className={`setup-scene-job${picked === i ? " is-picked" : ""}`} aria-hidden>
            <rect x={j.x - 64} y={j.y - 18} width="128" height="36" rx="10" />
            <rect className="setup-scene-job-lit" x={j.x - 64} y={j.y - 18} width="128" height="36" rx="10" opacity="0">
              <animate
                attributeName="opacity"
                values="0;1;0;0"
                keyTimes="0;0.12;0.3;1"
                dur="2.4s"
                repeatCount="indefinite"
                begin={`${i * 0.6 + 2}s`}
              />
            </rect>
            <image href={DUCKS[i].img} x={j.x - 58} y={j.y - 14} width="28" height="28" />
            <text x={j.x - 24} y={j.y + 5}>
              {DUCKS[i].job}
            </text>
          </g>
        ))}

        <g aria-hidden>
          <circle className="setup-scene-halo" cx={HUB.x} cy={HUB.y} r="48">
            <animate attributeName="r" values="44;54;44" dur="3s" repeatCount="indefinite" />
            <animate attributeName="opacity" values="0.18;0.04;0.18" dur="3s" repeatCount="indefinite" />
          </circle>
          <circle className="setup-scene-hub" cx={HUB.x} cy={HUB.y} r="40" />
          <g clipPath="url(#setup-scene-hub)">
            <image href="./OnlineMCPIcon.png" x={HUB.x - 32} y={HUB.y - 32} width="64" height="64">
              <animateTransform attributeName="transform" type="translate" values="0 0;0 -3;0 0" dur="2.4s" repeatCount="indefinite" />
            </image>
          </g>
          <circle className="setup-scene-spin" cx={HUB.x} cy={HUB.y} r="40" strokeDasharray="44 208">
            <animateTransform
              attributeName="transform"
              type="rotate"
              from={`0 ${HUB.x} ${HUB.y}`}
              to={`360 ${HUB.x} ${HUB.y}`}
              dur="2.4s"
              repeatCount="indefinite"
            />
          </circle>
        </g>

        <line className="setup-scene-ground" x1="16" y1={GROUND + 18} x2={W - 16} y2={GROUND + 18} aria-hidden />

        {DUCKS.map((d, i) => (
          <g key={d.id}>
            <animateTransform
              attributeName="transform"
              type="translate"
              from={`-48 ${GROUND - 20}`}
              to={`${W + 8} ${GROUND - 20}`}
              dur={`${WALK_S}s`}
              begin={`${-(WALK_S / DUCKS.length) * i}s`}
              repeatCount="indefinite"
            />
            <g
              className={`setup-scene-duck${picked === i ? " is-picked" : ""}`}
              role="button"
              tabIndex={0}
              aria-label={`${d.name} ducky: ${d.says}`}
              onMouseEnter={() => hold(true)}
              onMouseLeave={() => hold(false)}
              onFocus={() => hold(true)}
              onBlur={() => hold(false)}
              onClick={() => setPicked(i)}
              onKeyDown={(e) => {
                if (e.key === "Enter" || e.key === " ") {
                  e.preventDefault();
                  setPicked(i);
                }
              }}
            >
              <ellipse className="setup-scene-shadow" cx="20" cy="38" rx="14" ry="3" />
              <g className="setup-scene-waddle">
                <animateTransform
                  attributeName="transform"
                  type="rotate"
                  values="-6 20 36;6 20 36;-6 20 36"
                  dur="0.7s"
                  begin={`${i * 0.17}s`}
                  repeatCount="indefinite"
                />
                <image href={d.img} x="0" y="0" width="40" height="40" />
              </g>
            </g>
          </g>
        ))}
      </svg>
      <p className={`setup-scene-says${said ? " is-on" : ""}`} aria-live="polite">
        {said ? (
          <>
            <strong>{said.name}:</strong> {said.says}
          </>
        ) : (
          "Click a ducky to meet it"
        )}
      </p>
    </div>
  );
}
