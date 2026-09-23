// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { DRAG_OVERLAY_IDLE_MS, useDragOverlayIdle } from "./useDragOverlayIdle";

function Probe({ release }: { release: () => void }) {
  const { bump, cancel } = useDragOverlayIdle(release);
  return (
    <>
      <button type="button" onClick={bump}>bump</button>
      <button type="button" onClick={cancel}>cancel</button>
    </>
  );
}

afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

it("hides the drop box after dragover goes quiet", () => {
  vi.useFakeTimers();
  const release = vi.fn();
  render(<Probe release={release} />);
  fireEvent.click(screen.getByText("bump"));
  act(() => {
    vi.advanceTimersByTime(DRAG_OVERLAY_IDLE_MS - 1);
  });
  expect(release).not.toHaveBeenCalled();
  act(() => {
    vi.advanceTimersByTime(1);
  });
  expect(release).toHaveBeenCalledOnce();
});

it("keeps the box while dragover keeps arriving", () => {
  vi.useFakeTimers();
  const release = vi.fn();
  render(<Probe release={release} />);
  fireEvent.click(screen.getByText("bump"));
  act(() => {
    vi.advanceTimersByTime(200);
  });
  fireEvent.click(screen.getByText("bump"));
  act(() => {
    vi.advanceTimersByTime(200);
  });
  expect(release).not.toHaveBeenCalled();
  act(() => {
    vi.advanceTimersByTime(80);
  });
  expect(release).toHaveBeenCalledOnce();
});

it("cancel stops the hide so a drop can still read its target", () => {
  vi.useFakeTimers();
  const release = vi.fn();
  render(<Probe release={release} />);
  fireEvent.click(screen.getByText("bump"));
  fireEvent.click(screen.getByText("cancel"));
  act(() => {
    vi.advanceTimersByTime(DRAG_OVERLAY_IDLE_MS + 50);
  });
  expect(release).not.toHaveBeenCalled();
});
