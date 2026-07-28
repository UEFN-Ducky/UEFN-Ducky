import { describe, expect, it } from "vitest";
import type { ChatPlan, PlanNode } from "../types/panel";
import { findNodePath, planAtFocus, progressForNodes } from "./planOutlineNav";

const nodes: PlanNode[] = [
  {
    id: "a",
    content: "Combat",
    status: "pending",
    children: [
      {
        id: "a1",
        content: "Damage",
        status: "completed",
        children: [{ id: "a1a", content: "Knockback", status: "pending", children: [] }],
      },
    ],
  },
  { id: "b", content: "Arena", status: "pending", children: [] },
];

const plan: ChatPlan = {
  id: "p1",
  chat_id: "c1",
  title: "Arena Duel",
  overview: "Fight",
  body_markdown: "# Body",
  nodes,
  todos: [],
};

describe("findNodePath", () => {
  it("returns path to nested node", () => {
    expect(findNodePath(nodes, "a1a")?.map((n) => n.id)).toEqual(["a", "a1", "a1a"]);
  });
});

describe("planAtFocus", () => {
  it("roots the view at a subplan", () => {
    const view = planAtFocus(plan, "a");
    expect(view.title).toBe("Combat");
    expect(view.nodes?.map((n) => n.id)).toEqual(["a1"]);
    expect(view.overview).toBe("");
  });
});

describe("progressForNodes", () => {
  it("counts the whole subtree", () => {
    const p = progressForNodes(nodes);
    expect(p.total).toBe(4);
    expect(p.completed).toBe(1);
  });
});
