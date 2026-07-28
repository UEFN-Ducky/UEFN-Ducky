import { describe, expect, it } from "vitest";

import { resolveChatHoverTitle, resolveEditorChatTab } from "./ChatTabHoverCard";

describe("resolveEditorChatTab", () => {
  it("keeps isGroup from allChats for hub lookup", () => {
    const chat = resolveEditorChatTab(
      "hub-1",
      { name: "Squad", duckyStyle: "hacker" },
      [{ id: "hub-1", name: "Squad", isGroup: true, groupMembers: [] }],
    );
    expect(chat.isGroup).toBe(true);
  });

  it("falls back to tab.isGroup when hub is not in allChats yet", () => {
    const chat = resolveEditorChatTab("hub-1", { name: "Squad", isGroup: true }, []);
    expect(chat.isGroup).toBe(true);
    expect(chat.name).toBe("Squad");
  });
});

describe("resolveChatHoverTitle", () => {
  it("uses the created ducky/role name, not the avatar skin label", () => {
    expect(
      resolveChatHoverTitle(
        { name: "Prod", duckyName: "Producer", duckyStyle: "wizard" },
        "Wizard",
      ),
    ).toBe("Prod");
  });

  it("falls back to duckyName then style label", () => {
    expect(resolveChatHoverTitle({ name: "", duckyName: "Producer", duckyStyle: "wizard" }, "Wizard")).toBe(
      "Producer",
    );
    expect(resolveChatHoverTitle({ name: "", duckyStyle: "wizard" }, "Wizard")).toBe("Wizard");
  });
});
