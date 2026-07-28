/**
 * Product walkthrough overlay: dim hole + coachmark (Skip / Back / Next).
 * `require_click` steps punch a click-through hole; other steps block the page.
 */
import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { getTargetElement } from "../ui-targets/registry";
import {
  getActiveStep,
  getActiveSteps,
  getWalkthroughState,
  nextStep,
  prevStep,
  skipTour,
  subscribeWalkthrough,
} from "./WalkthroughService";
import "./walkthrough.css";

const PAD = 8;
const TIP_GAP = 12;
const TIP_W = 340;

function placeTooltip(
  hole: DOMRect | null,
  tipH: number,
): { x: number; y: number } {
  const vw = window.innerWidth;
  const vh = window.innerHeight;
  const maxW = Math.min(TIP_W, vw - 24);
  if (!hole) {
    return { x: Math.max(12, (vw - maxW) / 2), y: Math.max(12, vh * 0.3) };
  }
  let x = hole.left + hole.width / 2 - maxW / 2;
  x = Math.max(12, Math.min(x, vw - maxW - 12));
  let y = hole.bottom + TIP_GAP;
  if (y + tipH > vh - 12) {
    y = hole.top - tipH - TIP_GAP;
  }
  if (y < 12) y = 12;
  return { x, y };
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
  const stepTarget = step?.target;
  const stepAdvance = step?.advance;
  const stepMode = step?.mode;

  useEffect(() => {
    if (!active || !stepTarget) return;
    let raf = 0;
    const tick = () => {
      const el = getTargetElement(stepTarget);
      const root = rootRef.current;
      const circle = (stepMode ?? "rect") === "circle";
      if (root) {
        if (el) {
          const rect = el.getBoundingClientRect();
          const w = Math.max(24, rect.width + PAD * 2);
          const h = Math.max(24, rect.height + PAD * 2);
          const size = Math.max(w, h);
          const cx = rect.left + rect.width / 2;
          const cy = rect.top + rect.height / 2;
          const left = circle ? cx - size / 2 : rect.left - PAD;
          const top = circle ? cy - size / 2 : rect.top - PAD;
          const rw = circle ? size : w;
          const rh = circle ? size : h;
          root.style.setProperty("--wt-x", `${left}px`);
          root.style.setProperty("--wt-y", `${top}px`);
          root.style.setProperty("--wt-w", `${rw}px`);
          root.style.setProperty("--wt-h", `${rh}px`);
          root.style.setProperty("--wt-r", circle ? "50%" : "8px");
          const tip = placeTooltip(
            new DOMRect(left, top, rw, rh),
            tipHRef.current,
          );
          root.style.setProperty("--wt-tip-x", `${tip.x}px`);
          root.style.setProperty("--wt-tip-y", `${tip.y}px`);
          if (missingRef.current) {
            missingRef.current = false;
            setMissing(false);
          }

          // Punch click-through hole for require_click via clip-path on blocker.
          const blocker = root.querySelector(".walkthrough-blocker") as HTMLElement | null;
          if (blocker) {
            if (stepAdvance === "require_click") {
              const x = left;
              const y = top;
              const x2 = left + rw;
              const y2 = top + rh;
              blocker.style.clipPath = `polygon(evenodd, 0% 0%, 100% 0%, 100% 100%, 0% 100%, 0% 0%, ${x}px ${y}px, ${x}px ${y2}px, ${x2}px ${y2}px, ${x2}px ${y}px, ${x}px ${y}px)`;
            } else {
              blocker.style.clipPath = "none";
            }
          }
        } else {
          if (!missingRef.current) {
            missingRef.current = true;
            setMissing(true);
          }
          const tip = placeTooltip(null, tipHRef.current);
          root.style.setProperty("--wt-x", `0px`);
          root.style.setProperty("--wt-y", `0px`);
          root.style.setProperty("--wt-w", `0px`);
          root.style.setProperty("--wt-h", `0px`);
          root.style.setProperty("--wt-tip-x", `${tip.x}px`);
          root.style.setProperty("--wt-tip-y", `${tip.y}px`);
          const blocker = root.querySelector(".walkthrough-blocker") as HTMLElement | null;
          if (blocker) blocker.style.clipPath = "none";
        }
      }
      raf = requestAnimationFrame(tick);
    };
    tick();
    return () => cancelAnimationFrame(raf);
  }, [active, stepTarget, stepAdvance, stepMode, epoch]);

  // Bring the target into view when the step changes (settings content panes).
  useEffect(() => {
    if (!active || !stepTarget) return;
    const el = getTargetElement(stepTarget);
    if (!el) return;
    try {
      el.scrollIntoView({ block: "nearest", inline: "nearest", behavior: "smooth" });
    } catch {
      /* ignore */
    }
  }, [active, stepTarget, state.stepIndex]);

  useEffect(() => {
    if (!active || !stepTarget || stepAdvance !== "require_click") return;
    const el = getTargetElement(stepTarget);
    if (!el) return;
    const onClick = () => {
      void nextStep();
    };
    el.addEventListener("click", onClick, { capture: true });
    return () => el.removeEventListener("click", onClick, { capture: true });
  }, [active, stepTarget, stepAdvance, epoch]);

  useEffect(() => {
    if (!tipRef.current) return;
    const h = Math.round(tipRef.current.getBoundingClientRect().height);
    if (h > 0) tipHRef.current = h;
  }, [step?.title, step?.body, state.stepIndex, active]);

  if (!active || !step) return null;

  const isLast = state.stepIndex >= steps.length - 1;
  const requireClick = step.advance === "require_click";

  return createPortal(
    <div ref={rootRef} className="walkthrough-overlay" role="dialog" aria-modal="true" aria-label={step.title}>
      <div className="walkthrough-blocker" aria-hidden />
      <div className="walkthrough-hole" aria-hidden />
      <div className="walkthrough-ring" aria-hidden />
      <div ref={tipRef} className="walkthrough-tooltip">
        <button type="button" className="walkthrough-tooltip-skip" onClick={() => void skipTour()}>
          Skip
        </button>
        <h3 className="walkthrough-tooltip-title">{step.title}</h3>
        <p className="walkthrough-tooltip-body">{step.body}</p>
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
            {requireClick ? (
              <span className="walkthrough-tooltip-count">Click the highlight</span>
            ) : (
              <button
                type="button"
                className="walkthrough-btn walkthrough-btn-primary"
                onClick={() => void nextStep()}
              >
                {isLast ? "Got it" : "Next"}
              </button>
            )}
            {requireClick && missing ? (
              <button
                type="button"
                className="walkthrough-btn walkthrough-btn-primary"
                onClick={() => void nextStep()}
              >
                {isLast ? "Got it" : "Next"}
              </button>
            ) : null}
          </div>
        </div>
      </div>
    </div>,
    document.body,
  );
}
