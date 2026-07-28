import { describe, expect, it } from "vitest";

import { assistantTextFromRow } from "./speakReply";

describe("assistantTextFromRow", () => {
  it("reads load_messages text field (not content)", () => {
    expect(
      assistantTextFromRow({ role: "assistant", text: "Yes, I can hear you!" }),
    ).toBe("Yes, I can hear you!");
  });

  it("falls back to content if present", () => {
    expect(assistantTextFromRow({ role: "assistant", content: "legacy" })).toBe("legacy");
  });

  it("ignores non-assistant and empty rows", () => {
    expect(assistantTextFromRow({ role: "user", text: "hi" })).toBe("");
    expect(assistantTextFromRow({ role: "assistant", text: "  " })).toBe("");
  });
});
