import { describe, expect, it } from "vitest";

import type { FolderItem } from "../types/panel";
import { summarizeFolderContext } from "./folderContextSummary";

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

describe("summarizeFolderContext", () => {
  it("lists agents with models and sums their context tokens", () => {
    const art = folder({
      id: "art",
      name: "Art",
      groupHubId: "art-hub",
      chats: [
        {
          id: "m1",
          name: "Material Artist",
          model: "anthropic:claude-haiku-4-5",
          contextTokens: 2100,
        },
        { id: "m2", name: "3D Modeler", contextTokens: 3000 },
      ],
    });
    const summary = summarizeFolderContext(art);
    expect(summary.agentCount).toBe(2);
    expect(summary.totalTokens).toBe(5100);
    expect(summary.agents.map((a) => a.name)).toEqual(["Material Artist", "3D Modeler"]);
    expect(summary.agents[0]?.model).toBe("anthropic:claude-haiku-4-5");
    expect(summary.subgroups).toEqual([]);
  });

  it("rolls subgroup totals into the parent and exposes agents for the flyout", () => {
    const root = folder({
      id: "root",
      name: "Roguelike Game",
      groupHubId: "root-hub",
      chats: [{ id: "lead", name: "Producer", model: "haiku", contextTokens: 1000 }],
      children: [
        folder({
          id: "art",
          name: "Art",
          groupHubId: "art-hub",
          chats: [
            { id: "m1", name: "Material Artist", model: "sonnet", contextTokens: 2000 },
            { id: "m2", name: "3D Modeler", model: "sonnet", contextTokens: 3000 },
          ],
        }),
        folder({
          id: "prog",
          name: "Programming",
          groupHubId: "prog-hub",
          chats: [{ id: "p1", name: "Verse Dev", model: "opus", contextTokens: 4000 }],
        }),
      ],
    });
    const summary = summarizeFolderContext(root);
    expect(summary.agentCount).toBe(4);
    expect(summary.totalTokens).toBe(10000);
    expect(summary.subgroups).toEqual([
      {
        id: "art",
        name: "Art",
        contextTokens: 5000,
        agentCount: 2,
        agents: [
          {
            id: "m1",
            name: "Material Artist",
            duckyStyle: undefined,
            model: "sonnet",
            codingAgent: undefined,
            contextTokens: 2000,
          },
          {
            id: "m2",
            name: "3D Modeler",
            duckyStyle: undefined,
            model: "sonnet",
            codingAgent: undefined,
            contextTokens: 3000,
          },
        ],
      },
      {
        id: "prog",
        name: "Programming",
        contextTokens: 4000,
        agentCount: 1,
        agents: [
          {
            id: "p1",
            name: "Verse Dev",
            duckyStyle: undefined,
            model: "opus",
            codingAgent: undefined,
            contextTokens: 4000,
          },
        ],
      },
    ]);
  });
});
