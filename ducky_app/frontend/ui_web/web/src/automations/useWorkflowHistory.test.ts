// @vitest-environment jsdom
import { act, renderHook } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { useWorkflowHistory } from "./useWorkflowHistory";
import type { AutomationDto } from "../types/panel";

const doc = (): AutomationDto => ({ id: "one", kind: "pipeline", name: "Original", enabled: true, graph: { nodes: [{ id: "a", type: "flow.wait", x: 0, y: 0, config: {} }], edges: [] } });
describe("workflow edit history", () => {
  it("coalesces a gesture, survives save acknowledgement and preserves run metadata on undo", () => {
    const { result } = renderHook(useWorkflowHistory);
    act(() => result.current.reset(doc()));
    act(() => result.current.begin());
    for (const x of [10, 20, 30]) act(() => result.current.setDraft(current => ({ ...current!, graph: { ...current!.graph, nodes: [{ ...current!.graph.nodes[0], x }] } })));
    act(() => result.current.end());
    expect(result.current.entries).toHaveLength(2);
    act(() => result.current.replace(current => ({ ...current!, last_run: 123 })));
    act(() => { result.current.go(0); });
    expect(result.current.draft?.graph.nodes[0].x).toBe(0);
    expect(result.current.draft?.last_run).toBe(123);
    act(() => { result.current.go(1); });
    expect(result.current.draft?.graph.nodes[0].x).toBe(30);
  });
  it("branches after undo, coalesces typing, and resets when opening another workflow", () => {
    const { result } = renderHook(useWorkflowHistory);
    act(() => result.current.reset(doc()));
    act(() => result.current.begin());
    for (const name of ["N", "Ne", "New"]) act(() => result.current.setDraft(current => ({ ...current!, name })));
    act(() => result.current.end());
    expect(result.current.entries).toHaveLength(2);
    act(() => { result.current.go(0); });
    act(() => result.current.setDraft(current => ({ ...current!, enabled: false })));
    expect(result.current.entries).toHaveLength(2);
    expect(result.current.draft?.name).toBe("Original");
    act(() => result.current.reset({ ...doc(), id: "two" }));
    expect(result.current.entries).toHaveLength(1);
    expect(result.current.go(1)).toBeNull();
  });
  it("names custom code steps: convert, typing in the code, revert", () => {
    const { result } = renderHook(useWorkflowHistory);
    act(() => result.current.reset(doc()));
    const setNode = (patch: Record<string, unknown>) => act(() => result.current.setDraft(current => ({ ...current!, graph: { ...current!.graph, nodes: [{ ...current!.graph.nodes[0], ...patch }] } })));
    setNode({ type: "code.js", config: { code: "a", based_on: { type: "flow.wait", config: {} } } });
    setNode({ config: { code: "ab", based_on: { type: "flow.wait", config: {} } } });
    setNode({ type: "flow.wait", config: {} });
    expect(result.current.entries.map((entry) => entry.label)).toEqual(["Opened workflow", "Edit as custom code", "Edit code", "Revert to built-in"]);
  });
  it("undoes graph, settings, membership, and saved-version restores without mutating earlier snapshots", () => {
    const { result } = renderHook(useWorkflowHistory);
    act(() => result.current.reset(doc()));
    act(() => result.current.setDraft(current => ({ ...current!, graph: { ...current!.graph, nodes: [{ ...current!.graph.nodes[0], config: { seconds: 9 } }], groups: [{ id: "g", name: "Setup", node_ids: ["a"] }] } })));
    const grouped = result.current.draft;
    act(() => result.current.setDraft({ ...doc(), name: "Restored" }, "Restore saved version"));
    act(() => { result.current.go(1); });
    expect(result.current.draft?.graph).toEqual(grouped?.graph);
    act(() => { result.current.go(0); });
    expect(result.current.draft?.graph).toEqual(doc().graph);
    expect(grouped?.graph.groups?.[0].name).toBe("Setup");
  });
});
