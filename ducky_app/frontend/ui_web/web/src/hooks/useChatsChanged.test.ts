import { describe, expect, it } from "vitest";
import { shouldOpenCreatedChat } from "./useChatsChanged";

describe("shouldOpenCreatedChat", () => {
  it("opens a new chat by default but not when open is false", () => {
    expect(shouldOpenCreatedChat({ type: "chats_changed", conv_id: "g1" })).toBe(true);
    expect(shouldOpenCreatedChat({ type: "chats_changed", conv_id: "g1", open: false })).toBe(false);
    expect(shouldOpenCreatedChat({ type: "chats_changed", open: false })).toBe(false);
  });
});
