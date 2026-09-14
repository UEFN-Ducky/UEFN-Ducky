import { afterEach, describe, expect, it } from "vitest";
import {
  effectiveRailOpen,
  readOverlayRailSession,
  resetOverlayRailSession,
  setOverlayRailOpen,
  toggleOverlayRail,
} from "./overlayRailSession";

describe("effectiveRailOpen", () => {
  it("uses persisted state on desktop", () => {
    expect(effectiveRailOpen({ overlay: false, persisted: false, session: true })).toBe(false);
    expect(effectiveRailOpen({ overlay: false, persisted: true, session: false })).toBe(true);
  });

  it("ignores persisted open on overlay and uses session (closed by default)", () => {
    expect(effectiveRailOpen({ overlay: true, persisted: true, session: false })).toBe(false);
    expect(effectiveRailOpen({ overlay: true, persisted: true, session: true })).toBe(true);
  });

  it("does not reopen a closed rail just because layoutMode is full", () => {
    const leftRailOpen = false;
    const layoutMode = "full";
    const persistedLeft = leftRailOpen && layoutMode !== "sidebarHidden";
    expect(effectiveRailOpen({ overlay: false, persisted: persistedLeft, session: false })).toBe(
      false,
    );
  });
});

describe("overlayRailSession", () => {
  afterEach(() => {
    resetOverlayRailSession();
  });

  it("starts closed", () => {
    expect(readOverlayRailSession()).toEqual({ left: false, right: false });
  });

  it("opening one drawer closes the other", () => {
    setOverlayRailOpen("left", true);
    expect(readOverlayRailSession()).toEqual({ left: true, right: false });
    toggleOverlayRail("right");
    expect(readOverlayRailSession()).toEqual({ left: false, right: true });
  });
});
