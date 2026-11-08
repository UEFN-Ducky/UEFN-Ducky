import { describe, expect, it } from "vitest";
import { initialRemoteView, motionToCursor, padDown, padHoldStartsDrag, padMove, padUp, scrollSteps, takeWhole } from "./remoteTrackpadMath";

const TUNE = { sensitivity: 1, invertX: false, invertY: false };

describe("opening on the trackpad", () => {
  it("only the trackpad can be asked for by URL, never a window", () => {
    expect(initialRemoteView("?direct=1&view=trackpad")).toBe("trackpad");
    expect(initialRemoteView("?direct=1")).toBe("");
    expect(initialRemoteView("?view=12345")).toBe("");
  });
});

describe("pad gestures", () => {
  it("a still touch is a click and moves nothing", () => {
    const t = padDown(10, 10);
    expect(padMove(t, 13, 12, 2)).toEqual({ dx: 0, dy: 0 });
    expect(padUp(t)).toBe("click");
  });

  it("a slide moves the cursor, is not a click, and cannot become a drag", () => {
    const t = padDown(0, 0);
    expect(padMove(t, 20, 0, 2)).toEqual({ dx: 40, dy: 0 });
    expect(padMove(t, 25, 5, 2)).toEqual({ dx: 10, dy: 10 });
    expect(padHoldStartsDrag(t)).toBe(false);
    expect(padUp(t)).toBe("none");
  });

  it("a hold becomes a drag, which then moves and drops", () => {
    const t = padDown(0, 0);
    expect(padHoldStartsDrag(t)).toBe(true);
    t.dragging = true;
    expect(padMove(t, 30, 0, 1).dx).toBe(30);
    expect(padUp(t)).toBe("drop");
  });
});

describe("scroll and pixels", () => {
  it("counts whole notches both ways and keeps the rest", () => {
    expect(scrollSteps(0, 10)).toEqual({ steps: 0, acc: 10 });
    expect(scrollSteps(10, 40)).toEqual({ steps: 2, acc: 2 });
    expect(scrollSteps(0, -30)).toEqual({ steps: -1, acc: -6 });
  });

  it("keeps the fraction of a slow move", () => {
    expect(takeWhole(1.5, -1.25)).toEqual({ dx: 1, dy: -1, restX: 0.5, restY: -0.25 });
  });
});

describe("motion", () => {
  const flat = { x: 0, y: 0, z: 9.8 };
  const upright = { x: 0, y: 9.8, z: 0 };

  it("ignores hand tremble inside the deadzone", () => {
    const out = motionToCursor({ alpha: 1.5, beta: -1.5, gamma: 1 }, flat, 0.1, TUNE);
    expect(Math.abs(out.dx)).toBe(0);
    expect(Math.abs(out.dy)).toBe(0);
  });

  it("turning right moves right however the phone is held", () => {
    expect(motionToCursor({ alpha: -12, beta: 0, gamma: 0 }, flat, 1, TUNE).dx).toBeCloseTo(240);
    expect(motionToCursor({ alpha: 0, beta: 0, gamma: -12 }, upright, 1, TUNE).dx).toBeCloseTo(240);
  });

  it("gives the same answer when a browser reports gravity flipped", () => {
    const flipped = { x: 0, y: 0, z: -9.8 };
    expect(motionToCursor({ alpha: -12, beta: 0, gamma: 0 }, flipped, 1, TUNE).dx).toBeCloseTo(240);
  });

  it("tilting up moves up, and the invert settings flip each axis", () => {
    expect(motionToCursor({ alpha: 0, beta: 12, gamma: 0 }, flat, 1, TUNE).dy).toBeCloseTo(-240);
    expect(motionToCursor({ alpha: 0, beta: 12, gamma: 0 }, flat, 1, { ...TUNE, invertY: true }).dy).toBeCloseTo(240);
    expect(motionToCursor({ alpha: -12, beta: 0, gamma: 0 }, flat, 1, { ...TUNE, invertX: true }).dx).toBeCloseTo(-240);
  });
});
