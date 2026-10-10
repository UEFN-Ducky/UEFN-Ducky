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

describe("non-cancelled plan progress", () => {
  it.each(["nodes", "legacy", "server"])("uses the same header and popup counts for %s", source => {
    const value = source === "legacy" ? { ...plan, nodes: [], todos: [nodes[0]!, nodes[0]!.children![0]!, nodes[1]!].map(({ id, content, status }) => ({ id, content, status })) } : plan;
    const progress = source === "server" ? progressForPlan(value) : undefined;
    render(<><PlanTodoCard plan={value} progress={progress} /><ChatPlanPopup plan={value} progress={progress} /></>);
    expect(screen.getByText("1 of 2 steps")).toBeTruthy();
    expect(screen.getByText("1/2")).toBeTruthy();
    expect(screen.getByText("Cancelled")).toBeTruthy();
  });
  it("counts a focused subtree without its cancelled step", () => {
    expect(progressForNodes(nodes[0]!.children)).toEqual({ total: 0, completed: 0, cancelled: 1, in_progress: 0, pending: 0 });
    expect(progressForNodes([]).total).toBe(0);
  });
  it("marks the header and popup finished when all remaining work is completed", () => {
    const finished = { ...plan, nodes: [nodes[0]!] };
    render(<><PlanTodoCard plan={finished} /><ChatPlanPopup plan={finished} /></>);
    expect(screen.getByText("1 of 1 steps completed")).toBeTruthy();
    expect(screen.getByText("Finished")).toBeTruthy();
  });
});
