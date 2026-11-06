import { describe, expect, it } from "vitest";
import type { EditorTab } from "../types/panel";
import { chatTabsKey, syncChatTabTitles } from "./chatTabTitles";

const chats = [{ id: "2cc9d282-c459", name: "Live and Source Control", duckyStyle: "verse" }];

describe("chat tab titles", () => {
  it("a chat opened by id alone gets its name from the list it is already in", () => {
    // requestOpenChatTab(id) with no name names the tab after the raw id.
    const tabs: EditorTab[] = [{ id: "chat:2cc9d282-c459", kind: "chat", name: "2cc9d282-c459", chatId: "2cc9d282-c459" }];
    expect(syncChatTabTitles(tabs, chats)?.[0]).toMatchObject({ name: "Live and Source Control", duckyStyle: "verse" });
  });

  it("changes nothing when every tab already matches", () => {
    const tabs: EditorTab[] = [{ id: "chat:2cc9d282-c459", kind: "chat", name: "Live and Source Control", chatId: "2cc9d282-c459", duckyStyle: "verse", isGroup: false }];
    expect(syncChatTabTitles(tabs, chats)).toBeNull();
  });

  it("the key changes when a chat tab opens, so the sync runs again", () => {
    const before: EditorTab[] = [];
    const after: EditorTab[] = [{ id: "chat:2cc9d282-c459", kind: "chat", name: "2cc9d282-c459", chatId: "2cc9d282-c459" }];
    expect(chatTabsKey(after)).not.toBe(chatTabsKey(before));
    const file: EditorTab = { id: "file:a", kind: "file", name: "a", path: "a" };
    expect(chatTabsKey([...after, file])).toBe(chatTabsKey(after)); // other tabs don't count
  });
});
