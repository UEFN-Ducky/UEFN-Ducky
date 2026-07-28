import { describe, expect, it } from "vitest";

import {
  computeEditorTabHoverCardPosition,
  EDITOR_TAB_HOVER_CARD_EST_HEIGHT,
  EDITOR_TAB_HOVER_CARD_FILE_EST_HEIGHT,
  EDITOR_TAB_HOVER_CARD_WIDTH,
} from "./editorTabHoverCardPosition";

const viewport = { width: 1200, height: 800 };

describe("computeEditorTabHoverCardPosition", () => {
  it("opens to the left of a right-rail row so the card clears the sidebar", () => {
    const rect = { left: 900, right: 1180, top: 200, bottom: 224 };
    const pos = computeEditorTabHoverCardPosition(rect, "left", viewport);
    expect(pos.left).toBe(rect.left - EDITOR_TAB_HOVER_CARD_WIDTH - 6);
    expect(pos.left + EDITOR_TAB_HOVER_CARD_WIDTH).toBeLessThanOrEqual(rect.left);
  });

  it("flips left when prefer-right would clip past the window edge", () => {
    const rect = { left: 900, right: 1180, top: 200, bottom: 224 };
    const pos = computeEditorTabHoverCardPosition(rect, "right", viewport);
    expect(pos.left).toBe(rect.left - EDITOR_TAB_HOVER_CARD_WIDTH - 6);
  });

  it("keeps prefer-right when there is room beside a left rail", () => {
    const rect = { left: 8, right: 280, top: 120, bottom: 144 };
    const pos = computeEditorTabHoverCardPosition(rect, "right", viewport);
    expect(pos.left).toBe(rect.right + 6);
  });

  it("keeps short file cards aligned with a low UEFN Core row", () => {
    // Row near the bottom (Duckies/History/Outline sit below the Files panel).
    const rect = { left: 8, right: 280, top: 680, bottom: 702 };
    const pos = computeEditorTabHoverCardPosition(
      rect,
      "right",
      viewport,
      EDITOR_TAB_HOVER_CARD_FILE_EST_HEIGHT,
    );
    expect(pos.top).toBe(rect.top);
  });

  it("still nudges tall chat cards up when they would clip the bottom", () => {
    const rect = { left: 8, right: 280, top: 680, bottom: 702 };
    const pos = computeEditorTabHoverCardPosition(
      rect,
      "right",
      viewport,
      EDITOR_TAB_HOVER_CARD_EST_HEIGHT,
    );
    expect(pos.top).toBe(viewport.height - EDITOR_TAB_HOVER_CARD_EST_HEIGHT - 8);
    expect(pos.top).toBeLessThan(rect.top);
  });
});
