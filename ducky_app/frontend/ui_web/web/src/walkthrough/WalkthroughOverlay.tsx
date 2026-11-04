/**
 * Product walkthrough overlay: dim hole + coachmark (End tour / Back / Next).
 * `require_click` steps punch a click-through hole; other steps block the page.
 * Pressing the overlay never closes a menu the tour opened (`data-dropdown-keep`).
 */
import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { clipOutside, holeFor, placeCard } from "../ui-targets/geometry";
import { resolveTarget, revealTarget, type TargetSpec } from "../ui-targets/resolve";
import { MarkdownContent } from "../components/rich-content/MarkdownContent";
import {
  getActiveStep,
  getActiveSteps,
  getWalkthroughState,
  isStepEntered,
  nextStep,
  prevStep,
  skipTour,
  subscribeWalkthrough,
} from "./WalkthroughService";
import { startSpotlightLoop } from "./spotlightLoop";
import "./walkthrough.css";

const TIP_W = 340;

function placeTooltip(hole: DOMRect | null, tipH: number): { x: number; y: number } {
  return placeCard(hole, TIP_W, tipH, "below");
}

export function WalkthroughOverlay() {
  const [epoch, setEpoch] = useState(0);
  const rootRef = useRef<HTMLDivElement | null>(null);
  const tipRef = useRef<HTMLDivElement | null>(null);
  // tip height is ref-only — setState here re-rendered every measure and could
  // oscillate with layout (React #185). rAF already reads tipHRef.
  const tipHRef = useRef(160);
  const [missing, setMissing] = useState(false);
  const missingRef = useRef(false);

  useEffect(() => {
    return subscribeWalkthrough(() => setEpoch((n) => n + 1));
  }, []);

  const state = getWalkthroughState();
  const step = getActiveStep();
  const steps = getActiveSteps();
  const active = state.active && !!step;
  // A role/name spec when the step has one, else its id.
  const stepTarget: TargetSpec | undefined = step?.spec ?? step?.target;
  const stepAdvance = step?.advance;
  const stepMode = step?.mode;

  useEffect(() => {
    if (!active || !stepTarget) return;
    // What the spotlight shows now; written only when it changes (see spotlightLoop).
    let vars: Record<string, string> = {};
    let clip = "none";
    let found = false;
    const measure = (): string => {
      const el = resolveTarget(stepTarget);
      found = Boolean(el);
      if (el) {
        const hole = holeFor(el.getBoundingClientRect(), stepMode === "circle" ? "circle" : "rect");
        const { left, top, width: rw, height: rh } = hole;
        const tip = placeTooltip(new DOMRect(left, top, rw, rh), tipHRef.current);
        vars = {
          "--wt-x": `${left}px`, "--wt-y": `${top}px`, "--wt-w": `${rw}px`, "--wt-h": `${rh}px`,
          "--wt-r": hole.radius, "--wt-tip-x": `${tip.x}px`, "--wt-tip-y": `${tip.y}px`,
        };
        // Punch click-through hole for require_click via clip-path on blocker.
        clip = stepAdvance === "require_click" ? clipOutside(hole) : "none";
      } else {
        const tip = placeTooltip(null, tipHRef.current);
        vars = {
          "--wt-x": "0px", "--wt-y": "0px", "--wt-w": "0px", "--wt-h": "0px",
          "--wt-tip-x": `${tip.x}px`, "--wt-tip-y": `${tip.y}px`,
        };
        clip = "none";
      }
      return `${found}|${clip}|${Object.values(vars).join("|")}`;
    };
    const apply = () => {
      const root = rootRef.current;
      if (!root) return;
      for (const [name, value] of Object.entries(vars)) root.style.setProperty(name, value);
      if (missingRef.current === found) {
        missingRef.current = !found;
        setMissing(!found);
      }
      const blocker = root.querySelector(".walkthrough-blocker") as HTMLElement | null;
      if (blocker) blocker.style.clipPath = clip;
    };
    const loop = startSpotlightLoop(() => (rootRef.current ? measure() : ""), apply);
    const wake = () => loop.wake();
    window.addEventListener("resize", wake);
    window.addEventListener("scroll", wake, true);
    return () => {
      loop.stop();
      window.removeEventListener("resize", wake);
      window.removeEventListener("scroll", wake, true);
    };
  }, [active, stepTarget, stepAdvance, stepMode, epoch]);

  // Bring the target into view once the step's own setup (open a view, select a node) is
  // done — revealing earlier lost to the view restoring its own camera or scroll.
  const entered = active && isStepEntered();
  useEffect(() => {
    if (!active || !stepTarget || !entered) return;
    if (!resolveTarget(stepTarget)) return;
    void revealTarget(stepTarget);
  }, [active, stepTarget, state.stepIndex, entered]);

  useEffect(() => {
    if (!active || !stepTarget || stepAdvance !== "require_click") return;
    const el = resolveTarget(stepTarget);
    if (!el) return;
    // Only the click this step asked for moves on: the next step can click the same control
    // (to reopen its menu) before this listener is gone.
    const at = state.stepIndex;
    const onClick = () => {
      if (getWalkthroughState().stepIndex === at) void nextStep();
    };
    el.addEventListener("click", onClick, { capture: true });
    return () => el.removeEventListener("click", onClick, { capture: true });
  }, [active, stepTarget, stepAdvance, epoch, state.stepIndex]);

  // A tour that ends on a menu step leaves no menu open behind it.
  const wasActive = useRef(false);
  useEffect(() => {
    if (wasActive.current && !active) document.body.dispatchEvent(new Event("pointerdown", { bubbles: true }));
    wasActive.current = active;
  }, [active]);

  useEffect(() => {
    if (!tipRef.current) return;
    const h = Math.round(tipRef.current.getBoundingClientRect().height);
    if (h > 0) tipHRef.current = h;
  }, [step?.title, step?.body, state.stepIndex, active]);

  if (!active || !step) return null;

  const isLast = state.stepIndex >= steps.length - 1;
  const requireClick = step.advance === "require_click";

  return createPortal(
    <div
      ref={rootRef}
      className="walkthrough-overlay"
      role="dialog"
      aria-modal="true"
      aria-label={step.title}
      data-dropdown-keep
    >
      <div className="walkthrough-blocker" aria-hidden />
      <div className="walkthrough-hole" aria-hidden />
      <div className="walkthrough-ring" aria-hidden />
      <div ref={tipRef} className="walkthrough-tooltip">
        <button type="button" className="walkthrough-tooltip-skip" onClick={() => void skipTour()}>
          End tour
        </button>
        <h3 className="walkthrough-tooltip-title">{step.title}</h3>
        <div className="walkthrough-tooltip-body"><MarkdownContent text={step.body} /></div>
        {missing ? (
          <p className="walkthrough-missing">Target not visible yet — open that area or press Next.</p>
        ) : null}
        <div className="walkthrough-tooltip-footer">
          <span className="walkthrough-tooltip-count">
            {state.stepIndex + 1} / {steps.length}
          </span>
          <div className="walkthrough-tooltip-actions">
            {state.stepIndex > 0 ? (
              <button type="button" className="walkthrough-btn" onClick={() => void prevStep()}>
                Back
              </button>
            ) : null}
            {/* A click step moves on when the highlight is clicked; Next steps past it. */}
            <button
              type="button"
              className={`walkthrough-btn${requireClick ? "" : " walkthrough-btn-primary"}`}
              onClick={() => void nextStep()}
            >
              {isLast ? "Got it" : "Next"}
            </button>
          </div>
        </div>
      </div>
    </div>,
    document.body,
  );
}
