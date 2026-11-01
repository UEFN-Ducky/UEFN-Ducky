/**
 * A ducky that drops in on a string at the top of each setup step, sways, and does a little
 * trick (with a line) when clicked. The Welcome step has its own scene, so it has none here.
 */
import { useEffect, useState } from "react";

export type StepDuckStep = "plugins" | "ai" | "defaults";

const DUCKS: Record<StepDuckStep, { img: string; name: string; lines: string[] }> = {
  plugins: {
    img: "./duckies/cowboy.png",
    name: "Cowboy",
    lines: ["Yeehaw, rounding up your plugins.", "Seventeen plugins, one lasso.", "Hold your horses, almost there."],
  },
  ai: {
    img: "./duckies/hacker.png",
    name: "Hacker",
    lines: ["Plug me into a brain, any brain.", "I promise I only hack Fortnite.", "Keys stay on this PC. Trust the hoodie."],
  },
  defaults: {
    img: "./duckies/wizard.png",
    name: "Wizard",
    lines: ["Pick your spells, I'll do the rest.", "Last step, then the magic starts.", "Tell me who sent you, I like gossip."],
  },
};

export function SetupStepDuck({ step }: { step: StepDuckStep }) {
  const duck = DUCKS[step];
  const [line, setLine] = useState<number | null>(null);
  const [trick, setTrick] = useState(0);

  // The bubble goes away by itself.
  useEffect(() => {
    if (line === null) return;
    const t = window.setTimeout(() => setLine(null), 2600);
    return () => window.clearTimeout(t);
  }, [line, trick]);

  return (
    <div className={`setup-step-duck is-${step}`}>
      <span className="setup-step-duck-string" aria-hidden />
      <button
        type="button"
        key={trick}
        className={`setup-step-duck-btn${trick ? " is-trick" : ""}`}
        aria-label={`${duck.name} ducky`}
        onClick={() => {
          setTrick((n) => n + 1);
          setLine((cur) => ((cur ?? -1) + 1) % duck.lines.length);
        }}
      >
        <img src={duck.img} alt="" draggable={false} />
      </button>
      {line !== null ? (
        <span className="setup-step-duck-says" role="status">
          {duck.lines[line]}
        </span>
      ) : null}
    </div>
  );
}
