import { describe, expect, it } from "vitest";

import {
  EDITOR_TAB_DRAG_MIME,
  PLAN_NEST_DRAG_MIME,
  beginEditorTabDrag,
  endEditorTabDrag,
  getLastDragScreenPoint,
  isEditorTabDrag,
  trackEditorTabDragPointer,
} from "./editorTabDrag";

function fakeEvent(types: string[], textPlain = ""): { dataTransfer: DataTransfer } {
  return {
    dataTransfer: {
      types,
      getData: (mime: string) => (mime === "text/plain" ? textPlain : ""),
    } as unknown as DataTransfer,
  };
}

describe("isEditorTabDrag", () => {
  it("accepts editor-tab MIME", () => {
    expect(isEditorTabDrag(fakeEvent([EDITOR_TAB_DRAG_MIME, "text/plain"]))).toBe(true);
  });

  it("rejects plan nest drags even when text/plain is present", () => {
    expect(
      isEditorTabDrag(
        fakeEvent([PLAN_NEST_DRAG_MIME, "text/plain"], "C:/proj::chat-1"),
      ),
    ).toBe(false);
  });

  it("rejects non-tab text/plain payloads when readable", () => {
    expect(isEditorTabDrag(fakeEvent(["text/plain"], "C:/proj::chat-1"))).toBe(false);
  });

  it("accepts cross-window duckyTab JSON", () => {
    const payload = JSON.stringify({ duckyTab: { id: "tab-1", kind: "file" } });
    expect(isEditorTabDrag(fakeEvent(["text/plain"], payload))).toBe(true);
  });
});

describe("trackEditorTabDragPointer", () => {
  it("keeps the last real position when dragend reports 0,0", () => {
    beginEditorTabDrag("tab-1", "group-1");
    trackEditorTabDragPointer({ screenX: 900, screenY: 400, clientX: 810, clientY: 350 });
    // WebView2 dragend after a drop outside the window.
    trackEditorTabDragPointer({ screenX: 0, screenY: 0, clientX: 0, clientY: 0 });
    expect(getLastDragScreenPoint()).toEqual({ screenX: 900, screenY: 400 });
    endEditorTabDrag();
  });
});
