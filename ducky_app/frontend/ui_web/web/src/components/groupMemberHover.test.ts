import { describe, expect, it } from "vitest";

import type { ChatTab, FolderItem, GroupMemberDto } from "../types/panel";
import { aiTypeLabel, resolveNestedGroupHoverRows, shortModelLabel } from "./groupMemberHover";

function folder(partial: Partial<FolderItem> & Pick<FolderItem, "id" | "name">): FolderItem {
  return {
    parentId: "",
    sortOrder: 0,
    expanded: true,
    chats: [],
    children: [],
    ...partial,
  };
}

describe("aiTypeLabel", () => {
  it("shows model only for ducky agent", () => {
    expect(aiTypeLabel("anthropic:claude-haiku-4-5", "ducky")).toBe("claude-haiku-4-5");
  });

  it("prefixes coding agent when set", () => {
    expect(aiTypeLabel("anthropic:claude-haiku-4-5", "cursor")).toBe("cursor · claude-haiku-4-5");
  });
});

describe("resolveNestedGroupHoverRows", () => {
  it("lists folder agents with model and context", () => {
    const folders = [
      folder({
        id: "prog",
        name: "Programming",
        groupHubId: "prog-hub",
        chats: [
          {
            id: "v1",
            name: "Verse Coder",
            model: "anthropic:claude-sonnet-4-5",
            codingAgent: "ducky",
            contextTokens: 1200,
          },
          {
            id: "u1",
            name: "UI Engineer",
            model: "openai:gpt-5",
            codingAgent: "cursor",
            contextTokens: 800,
          },
        ],
      }),
    ];
    const rows = resolveNestedGroupHoverRows(
      "prog-hub",
      folders,
      [],
      [],
      new Map(),
      () => "Ducky",
    );
    expect(rows.map((r) => r.name)).toEqual(["Verse Coder", "UI Engineer"]);
    expect(rows[0]?.model).toBe("anthropic:claude-sonnet-4-5");
    expect(rows[1]?.codingAgent).toBe("cursor");
    expect(rows.reduce((s, r) => s + r.contextTokens, 0)).toBe(2000);
  });

  it("falls back to hub groupMembers + allChats context", () => {
    const members: GroupMemberDto[] = [
      {
        member_conv_id: "v1",
        profile_id: "p1",
        name: "Verse Coder",
        model: "haiku",
      },
    ];
    const allChats: ChatTab[] = [
      {
        id: "prog-hub",
        name: "Programming",
        isGroup: true,
        groupMembers: members,
      },
      { id: "v1", name: "Verse Coder", contextTokens: 500, model: "haiku" },
    ];
    const rows = resolveNestedGroupHoverRows(
      "prog-hub",
      [],
      allChats,
      [],
      new Map(),
      () => "Ducky",
    );
    expect(rows).toHaveLength(1);
    expect(rows[0]?.contextTokens).toBe(500);
    expect(shortModelLabel(rows[0]?.model || "")).toBe("haiku");
  });
});
