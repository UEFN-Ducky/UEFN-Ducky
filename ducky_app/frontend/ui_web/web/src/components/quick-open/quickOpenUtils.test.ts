import { describe, expect, it } from "vitest";
import { fuzzyScore, rankChats, rankFiles } from "./quickOpenUtils";

describe("quick-open ranking", () => {
  it("finds virtual_pointer_device when typing virtual pointer", () => {
    const files = [
      { path: "Content/Verse/VirtualPointer/virtual_pointer_device.verse", name: "virtual_pointer_device.verse" },
      { path: "Content/Verse/DuckyTests/ducky_test_device.verse", name: "ducky_test_device.verse" },
    ];
    const hits = rankFiles("virtual pointer", files);
    expect(hits[0]?.name).toBe("virtual_pointer_device.verse");
    expect(fuzzyScore("virtual pointer", "virtual_pointer_device.verse")).toBeGreaterThan(0);
  });

  it("ranks duckies by name and personality, not title only", () => {
    const chats = [
      { id: "1", name: "General Helper", duckyName: "Helper", duckyPersonality: "virtual pointer canvas and traces" },
      { id: "2", name: "Shop", duckyName: "Shop" },
    ];
    const hits = rankChats("virtual pointer", chats);
    expect(hits.map((c) => c.id)).toEqual(["1"]);
  });
});
