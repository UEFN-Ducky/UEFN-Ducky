// @vitest-environment jsdom
import { act, cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

import { AgentActivityPanel } from "./AgentActivityPanel";

beforeEach(() => {
  vi.useFakeTimers();
  vi.setSystemTime(new Date("2026-11-18T00:36:45"));
});

afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

const panel = (done: boolean) => (
  <AgentActivityPanel lines={[]} isWaitingOnLinked={false} waitingCount={0} showIdleTimer={done} />
);

it("counts a new turn's quiet from its own start, not from the end of the turn before", () => {
  // Nov 18 2026: a turn ended at 00:36:45 and the panel stayed up as "Done". The next message
  // went out at 00:42:33 and the panel at once read "Thinking about the next step… 5m 50s quiet".
  const { rerender } = render(panel(true));
  act(() => {
    vi.advanceTimersByTime(348_000);
  });
  rerender(panel(false));
  expect(screen.queryByText(/quiet/)).toBeNull();
  expect(screen.queryByText(/Thinking about the next step/)).toBeNull();

  // Once the new turn itself has said nothing for a while, it does say so, with its own count.
  act(() => {
    vi.advanceTimersByTime(20_000);
  });
  expect(screen.getByText(/Thinking about the next step/)).toBeTruthy();
  expect(screen.getByText(/^20s quiet/)).toBeTruthy();
});
