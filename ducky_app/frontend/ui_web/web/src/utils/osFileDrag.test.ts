import { describe, expect, it } from "vitest";
import {
  CHAT_ATTACH_TARGET,
  dragHasOsFiles,
  isChatAttachDropTarget,
  OPEN_EXTERNAL_TARGET,
} from "./osFileDrag";

describe("osFileDrag", () => {
  it("detects OS file MIME type", () => {
    expect(dragHasOsFiles({ types: ["Files"] } as DataTransfer)).toBe(true);
    expect(dragHasOsFiles({ types: ["text/plain"] } as DataTransfer)).toBe(false);
    expect(dragHasOsFiles(null)).toBe(false);
  });

  it("rejects non-elements for chat attach targeting", () => {
    expect(isChatAttachDropTarget(null)).toBe(false);
    expect(isChatAttachDropTarget({} as EventTarget)).toBe(false);
  });

  it("keeps chat-attach sentinel distinct from open-external", () => {
    expect(CHAT_ATTACH_TARGET).not.toBe(OPEN_EXTERNAL_TARGET);
    expect(CHAT_ATTACH_TARGET.startsWith(":")).toBe(true);
  });
});
