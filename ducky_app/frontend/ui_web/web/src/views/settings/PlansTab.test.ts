import { describe, expect, it } from "vitest";
import { buildPlanForest, flattenOutline, wouldNestCycle } from "./PlansTab";
import type { PlanListItem, PlanNode } from "../../types/panel";

function item(chatId: string, title: string, nodes?: PlanNode[]): PlanListItem {
  return {
    chat_id: chatId,
    plan_id: chatId,
    title,
    progress: { total: 0, completed: 0, cancelled: 0, in_progress: 0, pending: 0 },
    updated_at: 0,
    project_root: "/proj",
    project_name: "proj",
    nodes,
  };
}

describe("flattenOutline", () => {
  it("numbers nested subplans 1, 1.1, 1.1.1, 2", () => {
    const nodes: PlanNode[] = [
      {
        id: "a",
        content: "Shell",
        status: "pending",
        children: [
          {
            id: "a1",
            content: "Floor",
            status: "pending",
            children: [{ id: "a1a", content: "Tiles", status: "pending", children: [] }],
          },
        ],
      },
      { id: "b", content: "Props", status: "pending", children: [] },
    ];
    const flat = flattenOutline(nodes);
    expect(flat.map((r) => r.label)).toEqual(["1", "1.1", "1.1.1", "2"]);
    expect(flat.map((r) => r.node.content)).toEqual(["Shell", "Floor", "Tiles", "Props"]);
  });
});

describe("buildPlanForest", () => {
  it("returns a flat list (no chat nesting)", () => {
    const forest = buildPlanForest([item("a", "A"), item("b", "B")]);
    expect(forest).toHaveLength(2);
    expect(forest.every((n) => n.depth === 0 && n.childCount === 0)).toBe(true);
  });
});

describe("wouldNestCycle", () => {
  it("always false — chat nesting removed", () => {
    expect(wouldNestCycle()).toBe(false);
  });
});
