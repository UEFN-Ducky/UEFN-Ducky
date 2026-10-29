// @vitest-environment jsdom
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { ChatPlan, PlanNode } from "../types/panel";
import { progressForPlan, progressForNodes } from "../utils/planOutlineNav";
import { PlanTodoCard } from "./PlanTodoCard";
import { ChatPlanPopup } from "./ChatPlanPopup";
vi.mock("./rich-content/RichContentRenderer", () => ({ RichContentRenderer: () => null }));
beforeEach(() => vi.stubGlobal("matchMedia", () => ({ matches: true })));
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });
const nodes: PlanNode[] = [
  { id: "a", content: "Done", status: "completed", children: [
    { id: "b", content: "Cancelled", status: "cancelled" },
  ] },
  { id: "c", content: "Next", status: "pending" },
];
const plan: ChatPlan = { id: "p", chat_id: "c", title: "Plan", nodes, todos: [] };

describe("plan progress counts cancelled steps as finished", () => {
  it.each(["nodes", "legacy", "server"])("uses the same header and popup counts for %s", source => {
    const value = source === "legacy" ? { ...plan, nodes: [], todos: [nodes[0]!, nodes[0]!.children![0]!, nodes[1]!].map(({ id, content, status }) => ({ id, content, status })) } : plan;
    const progress = source === "server" ? progressForPlan(value) : undefined;
    render(<><PlanTodoCard plan={value} progress={progress} /><ChatPlanPopup plan={value} progress={progress} /></>);
    expect(screen.getByText("2 of 3 steps")).toBeTruthy();
    expect(screen.getByText("2/3")).toBeTruthy();
    expect(screen.getByText("Cancelled")).toBeTruthy();
  });
  it("counts a focused subtree without its cancelled step", () => {
    expect(progressForNodes(nodes[0]!.children)).toEqual({ total: 0, completed: 0, cancelled: 1, in_progress: 0, pending: 0 });
    expect(progressForNodes([]).total).toBe(0);
  });
  it.each(["local", "server"])("shows full progress when every step is completed or cancelled (%s)", (source) => {
    const finished = { ...plan, nodes: [nodes[0]!] };
    const progress = source === "server" ? progressForPlan(finished) : undefined;
    render(<><PlanTodoCard plan={finished} progress={progress} /><ChatPlanPopup plan={finished} progress={progress} /></>);
    expect(screen.getByText("2 of 2 steps completed")).toBeTruthy();
    expect(screen.getByText("Finished")).toBeTruthy();
  });
  it("indents every nesting level, past the old cap of 6", () => {
    let deep: PlanNode = { id: "n9", content: "Level 9", status: "pending" };
    for (let i = 8; i >= 1; i--) deep = { id: `n${i}`, content: `Level ${i}`, status: "pending", children: [deep] };
    const { container } = render(<PlanTodoCard plan={{ ...plan, nodes: [deep] }} />);
    const row = container.querySelector<HTMLElement>('[data-plan-node-id="n9"]');
    expect(row?.className).toContain("plan-todo-row--nested");
    expect(row?.style.getPropertyValue("--plan-depth")).toBe("8");
  });
});
