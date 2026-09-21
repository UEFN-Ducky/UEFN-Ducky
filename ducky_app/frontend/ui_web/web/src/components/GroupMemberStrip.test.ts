import { describe, expect, it } from "vitest";

import { inviteableChats, laneTitle, shortLaneLabel, shortModelLabel, shortWhenToUse } from "./GroupMemberStrip";
import type { ChatTab, GroupMemberDto } from "../types/panel";

describe("shortWhenToUse", () => {
  it("keeps short blurbs", () => {
    expect(shortWhenToUse("Mesh and modeling work")).toBe("Mesh and modeling work");
  });

  it("truncates to ten words", () => {
    const long =
      "Mesh and modeling work — building or importing Static Meshes, merging pieces, UVs textures";
    expect(shortWhenToUse(long, 10)).toBe(
      "Mesh and modeling work — building or importing Static Meshes,…",
    );
  });
});

describe("lane badge", () => {
  it("labels the three lane states", () => {
    expect(shortLaneLabel(null)).toBe("No lane");
    expect(shortLaneLabel([])).toBe("Read-only");
    expect(shortLaneLabel(["Content/Verse/Shop/**"])).toBe("Verse/Shop/");
  });

  it("tooltip lists every glob", () => {
    expect(laneTitle(["a/**", "b.verse"])).toBe("Write lane:\na/**\nb.verse");
    expect(laneTitle([])).toContain("Read-only");
    expect(laneTitle(null)).toContain("No write lane");
  });
});

describe("shortModelLabel", () => {
  it("shows model id from qualified selection", () => {
    expect(shortModelLabel("anthropic:claude-sonnet-4-5")).toBe("claude-sonnet-4-5");
  });

  it("falls back for empty", () => {
    expect(shortModelLabel("")).toBe("Default model");
  });
});

describe("inviteableChats", () => {
  const verse: ChatTab = { id: "verse", name: "VerseDev" };
  const level: ChatTab = { id: "level", name: "LevelDesigner" };
  const seated: ChatTab = { id: "seated", name: "Seated" };
  const otherGroup: ChatTab = { id: "other", name: "Other", parentConvId: "hub-b" };
  const nested: ChatTab = { id: "nested", name: "Art Team", isGroup: true };
  const members: GroupMemberDto[] = [{ member_conv_id: "seated", name: "Seated", profile_id: "" }];

  it("lists root duckies that are not already in the group", () => {
    const ids = inviteableChats(
      [verse, level, seated, otherGroup, nested, { id: "hub", name: "Group1", isGroup: true }],
      members,
      "hub",
    ).map((c) => c.id);
    expect(ids).toEqual(["level", "verse"]);
  });
});
