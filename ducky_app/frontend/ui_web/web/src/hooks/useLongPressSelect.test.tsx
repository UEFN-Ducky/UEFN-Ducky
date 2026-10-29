// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { useLongPressSelect } from "./useLongPressSelect";

function Row({ onLongPress, onClick }: { onLongPress: () => void; onClick: () => void }) {
  const props = useLongPressSelect(onLongPress);
  return <div {...props} onClick={onClick}>row</div>;
}

const touch = { pointerType: "touch", pointerId: 1, button: 0 };

let onLongPress = vi.fn();
let onClick = vi.fn();
beforeEach(() => {
  vi.useFakeTimers();
  onLongPress = vi.fn();
  onClick = vi.fn();
  render(<Row onLongPress={onLongPress} onClick={onClick} />);
});
afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

describe("useLongPressSelect", () => {
  it("a 450 ms touch hold fires once and the click after it is swallowed", () => {
    const row = screen.getByText("row");
    fireEvent.pointerDown(row, { ...touch, clientX: 5, clientY: 5 });
    act(() => vi.advanceTimersByTime(449));
    expect(onLongPress).not.toHaveBeenCalled();
    act(() => vi.advanceTimersByTime(1));
    expect(onLongPress).toHaveBeenCalledOnce();
    fireEvent.pointerUp(row, touch);
    fireEvent.click(row);
    expect(onClick).not.toHaveBeenCalled();
    // The next plain tap clicks again.
    fireEvent.pointerDown(row, touch);
    fireEvent.pointerUp(row, touch);
    fireEvent.click(row);
    expect(onClick).toHaveBeenCalledOnce();
  });

  it("moving 20 px cancels the hold", () => {
    const row = screen.getByText("row");
    fireEvent.pointerDown(row, { ...touch, clientX: 5, clientY: 5 });
    fireEvent.pointerMove(row, { ...touch, clientX: 5, clientY: 25 });
    act(() => vi.advanceTimersByTime(1000));
    expect(onLongPress).not.toHaveBeenCalled();
  });

  it("a mouse hold does nothing new", () => {
    const row = screen.getByText("row");
    fireEvent.pointerDown(row, { pointerType: "mouse", pointerId: 1, button: 0 });
    act(() => vi.advanceTimersByTime(1000));
    fireEvent.click(row);
    expect(onLongPress).not.toHaveBeenCalled();
    expect(onClick).toHaveBeenCalledOnce();
  });

  it("the OS context menu and a drag during a hold are swallowed", () => {
    const row = screen.getByText("row");
    fireEvent.pointerDown(row, touch);
    expect(fireEvent.contextMenu(row)).toBe(false);
    expect(onLongPress).toHaveBeenCalledOnce();
    expect(fireEvent.dragStart(row)).toBe(false);
  });
});
