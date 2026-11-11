import { afterEach, describe, expect, it, vi } from "vitest";
import { markChatShown, onShownChatsChange, resetShownChatsForTests, shownChatIds } from "./shownChats";

afterEach(() => resetShownChatsForTests());

describe("shownChats", () => {
  it("lists a chat while any visible pane shows it", () => {
    const changes = vi.fn();
    onShownChatsChange(changes);
    const tab = markChatShown("b");
    const dock = markChatShown("a");
    expect(shownChatIds()).toEqual(["a", "b"]);
    expect(changes).toHaveBeenCalledTimes(2);
    // The same chat in a second pane (a tab and a plugin's dock) changes nothing.
    const second = markChatShown("a");
    expect(changes).toHaveBeenCalledTimes(2);
    dock();
    expect(shownChatIds()).toEqual(["a", "b"]);
    expect(changes).toHaveBeenCalledTimes(2);
    second();
    second(); // releasing twice is harmless
    expect(shownChatIds()).toEqual(["b"]);
    expect(changes).toHaveBeenCalledTimes(3);
    tab();
    expect(shownChatIds()).toEqual([]);
  });

  it("ignores a pane with no chat", () => {
    const changes = vi.fn();
    onShownChatsChange(changes);
    markChatShown("")();
    expect(shownChatIds()).toEqual([]);
    expect(changes).not.toHaveBeenCalled();
  });
});
