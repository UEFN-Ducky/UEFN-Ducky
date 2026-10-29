// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import type { PlanNode } from "../types/panel";
import { _resetBackgroundActivityForTests, upsertBackgroundJob } from "../hooks/backgroundActivity";
import { subPlanProgress, WorkProgressBar, type WorkRow } from "./WorkProgressBar";

const NOW = 1_000_000_000;
const MIN = 60_000;

beforeEach(() => {
  vi.useFakeTimers();
  vi.setSystemTime(NOW);
  _resetBackgroundActivityForTests();
});
afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

const step = (id: string, status: PlanNode["status"]): PlanNode => ({ id, content: id, status });
const one = (progress: WorkRow["progress"] = null): WorkRow[] => [{ convId: "verify", name: "Verify", progress }];
const bars = () => [...document.querySelectorAll("progress")] as HTMLProgressElement[];

it("counts the leaves of the section assigned to an agent", () => {
  const nodes: PlanNode[] = [
    step("lead", "completed"),
    {
      id: "s", content: "Verify", status: "in_progress", kind: "subplan", assignee: "verify",
      children: [step("a", "completed"), step("b", "cancelled"), step("c", "completed"), step("d", "pending"), step("e", "in_progress")],
    },
  ];
  expect(subPlanProgress(nodes, "verify")).toEqual({ done: 3, total: 5 });
  expect(subPlanProgress(nodes, "nobody")).toBeNull();
});

it("a waited-on agent with 3/5 steps shows a 60% bar, its detail and minutes; click opens it", () => {
  upsertBackgroundJob({ id: "j", convId: "verify", phase: "working", detail: "Running a script", startedAt: NOW - 11.5 * MIN });
  const onOpen = vi.fn();
  render(<WorkProgressBar rows={one({ done: 3, total: 5 })} onOpen={onOpen} />);
  expect(screen.getByText("· Running a script · 11 min", { exact: false })).toBeTruthy();
  expect(bars()).toHaveLength(1);
  expect(bars()[0].value / bars()[0].max).toBeCloseTo(0.6);
  fireEvent.click(screen.getByRole("button"));
  expect(onOpen).toHaveBeenCalledWith("verify");
});

it("no plan renders the indeterminate bar", () => {
  render(<WorkProgressBar rows={one()} onOpen={() => {}} />);
  expect(bars()[0].hasAttribute("value")).toBe(false);
});

it("an expected 15 min run 12 min in shows ~3 min left and an 80% run bar", () => {
  upsertBackgroundJob({ id: "j", convId: "verify", phase: "working", detail: "Running a script",
    startedAt: NOW - 12 * MIN, expectStartedAt: NOW - 12 * MIN, expectedMs: 15 * MIN });
  render(<WorkProgressBar rows={one()} onOpen={() => {}} />);
  expect(screen.getByText("· Running a script · 12 min · ~3 min left", { exact: false })).toBeTruthy();
  const run = bars()[1];
  expect(run.value / run.max).toBeCloseTo(0.8);
});

it("an estimated duration says about", () => {
  upsertBackgroundJob({ id: "j", convId: "verify", phase: "working", startedAt: NOW - 12 * MIN,
    expectStartedAt: NOW - 12 * MIN, expectedMs: 15 * MIN, estimated: true });
  render(<WorkProgressBar rows={one()} onOpen={() => {}} />);
  expect(screen.getByText("about 3 min left", { exact: false })).toBeTruthy();
});

it("past the expected time says taking longer and caps the run bar at 95%", () => {
  upsertBackgroundJob({ id: "j", convId: "verify", phase: "working", startedAt: NOW - 18 * MIN,
    expectStartedAt: NOW - 18 * MIN, expectedMs: 15 * MIN });
  render(<WorkProgressBar rows={one()} onOpen={() => {}} />);
  const text = screen.getByText("taking longer than expected · 18 min", { exact: false });
  expect(text.className).toBe("work-progress__late");
  const run = bars()[1];
  expect(run.value / run.max).toBeCloseTo(0.95);
});

it("shows three rows then +N more, and nothing when idle", () => {
  const rows: WorkRow[] = ["a", "b", "c", "d", "e"].map((id) => ({ convId: id, name: id, progress: null }));
  const { rerender, container } = render(<WorkProgressBar rows={rows} onOpen={() => {}} />);
  expect(screen.getAllByRole("button")).toHaveLength(3);
  expect(screen.getByText("+2 more")).toBeTruthy();
  rerender(<WorkProgressBar rows={[]} onOpen={() => {}} />);
  expect(container.innerHTML).toBe("");
});

it("ticks every 30 s only while shown and clears the interval on unmount", () => {
  const clear = vi.spyOn(window, "clearInterval");
  upsertBackgroundJob({ id: "j", convId: "a", phase: "working", startedAt: NOW });
  const { unmount } = render(<WorkProgressBar rows={[{ convId: "a", name: "A", progress: null }]} onOpen={() => {}} />);
  expect(screen.getByText("· <1 min", { exact: false })).toBeTruthy();
  act(() => { vi.advanceTimersByTime(MIN); });
  expect(screen.getByText("· 1 min", { exact: false })).toBeTruthy();
  expect(vi.getTimerCount()).toBeGreaterThan(0);
  unmount();
  expect(clear).toHaveBeenCalled();
  expect(vi.getTimerCount()).toBe(0);
});
