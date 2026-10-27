// @vitest-environment jsdom
import { afterEach, expect, it, vi } from "vitest";

const api = vi.hoisted(() => ({ load_messages: vi.fn() }));
vi.mock("./usePanelApi", () => ({ getApi: () => api }));

import type { ChatMessage } from "../types/panel";
import { fetchChatRows, firstLoadReport, spliceTail } from "./chatRowsFetch";

afterEach(() => vi.resetAllMocks());

it("a chat that keeps failing to load is reported once per kind", () => {
  expect(firstLoadReport("chat_failed", "chat-a")).toBe(true);
  expect(firstLoadReport("chat_failed", "chat-a")).toBe(false);
  expect(firstLoadReport("chat_timeout", "chat-a")).toBe(true);
  expect(firstLoadReport("chat_failed", "chat-b")).toBe(true);
});

it("a load that answers with nothing fails instead of handing the chat an undefined list", async () => {
  // Oct 11 2026: the phone app crashed opening a chat ("reading 'length'"): the remote
  // bridge answers a 502 or a dropped connection with undefined.
  api.load_messages.mockResolvedValue(undefined);
  await expect(fetchChatRows("chat-1")).rejects.toThrow("no messages came back");
  api.load_messages.mockResolvedValue([{ id: 1, role: "user", text: "hi" }]);
  await expect(fetchChatRows("chat-1")).resolves.toHaveLength(1);
});

it("a reload asks for the newest rows only and splices them onto what is shown", async () => {
  api.load_messages.mockResolvedValue([]);
  await fetchChatRows("chat-1", 1000, 3);
  expect(api.load_messages).toHaveBeenCalledWith("chat-1", null, 3);

  const row = (id: number, text: string): ChatMessage => ({ id, role: "assistant", text });
  const shown = [row(0, "a"), row(1000, "b"), row(2000, "c"), { id: "opt-1", role: "user", text: "go" } as ChatMessage];
  const page = [row(1000, "b"), row(2000, "c"), row(3000, "d")];
  expect(spliceTail(shown, page, 3)?.map((m) => m.text)).toEqual(["a", "b", "c", "d"]);
  // A short page is the whole chat.
  expect(spliceTail(shown, page.slice(2), 3)?.map((m) => m.text)).toEqual(["d"]);
  // Rewound (same id, other text) or starting before anything shown: load it all.
  expect(spliceTail(shown, [row(1000, "x"), ...page.slice(1)], 3)).toBeNull();
  expect(spliceTail(shown.slice(2), page, 3)).toBeNull();
});
