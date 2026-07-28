import { describe, expect, it } from "vitest";
import {
  DUCKTACTOE_CHAT_NAME,
  DUCKTACTOE_PERSONALITY,
  ducktactoeChatConfig,
  isDucktactoeBoardTab,
  isDucktactoeChat,
  shouldSuppressRemoteChatOpen,
} from "./ducktactoeBoardChat";

describe("ducktactoeBoardChat", () => {
  it("brands the game chat and recognizes board / chat", () => {
    const cfg = ducktactoeChatConfig();
    expect(cfg.title).toBe(DUCKTACTOE_CHAT_NAME);
    expect(cfg.ducky_name).toBe(DUCKTACTOE_CHAT_NAME);
    expect(cfg.ducky_personality).toBe(DUCKTACTOE_PERSONALITY);
    expect(cfg.coding_agent).toBe("ducky");
    expect(cfg.disabled_tool_ids).toEqual([]);
    expect(isDucktactoeBoardTab("ducktactoe", "board")).toBe(true);
    expect(isDucktactoeBoardTab("ducktactoe", "other")).toBe(false);
    expect(isDucktactoeChat({ name: DUCKTACTOE_CHAT_NAME })).toBe(true);
    expect(isDucktactoeChat({ duckyPersonality: DUCKTACTOE_PERSONALITY })).toBe(true);
    expect(isDucktactoeChat({ name: "Other" })).toBe(false);
    expect(shouldSuppressRemoteChatOpen({ id: "x", title: "Other" })).toBe(false);
  });
});
