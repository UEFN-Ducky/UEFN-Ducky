// @vitest-environment jsdom
import { act, cleanup, render } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { clockTimers, subscribeClock, useNow } from "./sharedClock";

let hidden = false;

beforeEach(() => {
  vi.useFakeTimers();
  hidden = false;
  Object.defineProperty(document, "hidden", { configurable: true, get: () => hidden });
});
afterEach(() => {
  cleanup();
  vi.useRealTimers();
  delete (document as { hidden?: boolean }).hidden;
});

const show = (on: boolean) => {
  hidden = !on;
  document.dispatchEvent(new Event("visibilitychange"));
};

it("everything on the same period shares one timer and hears the same time", () => {
  const set = vi.spyOn(window, "setInterval");
  const heard: Array<[string, number]> = [];
  const stops = ["a", "b", "c"].map((name) => subscribeClock(1000, (now) => heard.push([name, now])));
  const stopSlow = subscribeClock(5000, (now) => heard.push(["slow", now]));
  expect(set).toHaveBeenCalledTimes(2); // one for 1 s, one for 5 s
  expect(clockTimers()).toBe(2);
  vi.advanceTimersByTime(1000);
  expect(heard.map(([name]) => name)).toEqual(["a", "b", "c"]);
  expect(new Set(heard.map(([, now]) => now)).size).toBe(1);
  stops[0]();
  vi.advanceTimersByTime(1000);
  expect(heard.slice(3).map(([name]) => name)).toEqual(["b", "c"]);
  stops[1]();
  stops[2]();
  stopSlow();
  expect(clockTimers()).toBe(0); // the last listener gone: no timer left running
  const before = heard.length;
  vi.advanceTimersByTime(10_000);
  expect(heard).toHaveLength(before);
  set.mockRestore();
});

it("the same callback subscribed twice is two listeners", () => {
  const hear = vi.fn();
  const stopOne = subscribeClock(1000, hear);
  const stopTwo = subscribeClock(1000, hear);
  stopOne();
  vi.advanceTimersByTime(1000);
  expect(hear).toHaveBeenCalledTimes(1);
  stopTwo();
});

it("stops while the window is hidden and catches up at once when it is shown", () => {
  const hear = vi.fn();
  const stop = subscribeClock(1000, hear);
  show(false);
  vi.advanceTimersByTime(60_000);
  expect(hear).not.toHaveBeenCalled();
  show(true);
  expect(hear).toHaveBeenCalledTimes(1);
  vi.advanceTimersByTime(1000);
  expect(hear).toHaveBeenCalledTimes(2);
  stop();
});

it("one listener's error does not stop the others", () => {
  const good = vi.fn();
  const stopBad = subscribeClock(1000, () => {
    throw new Error("boom");
  });
  const stopGood = subscribeClock(1000, good);
  vi.advanceTimersByTime(1000);
  expect(good).toHaveBeenCalledTimes(1);
  stopBad();
  stopGood();
});

it("many running clocks draw in one pass a second, and none ticks once nothing runs", () => {
  let renders = 0;
  function Card({ running }: { running: boolean }) {
    const now = useNow(1000, running);
    renders += 1;
    return <span>{now}</span>;
  }
  const Cards = ({ running }: { running: boolean }) => (
    <>{Array.from({ length: 20 }, (_, i) => <Card key={i} running={running} />)}</>
  );
  const view = render(<Cards running />);
  expect(renders).toBe(20); // the mount only: turning on right at the mount adds no second draw
  expect(clockTimers()).toBe(1); // twenty cards, one timer
  act(() => {
    vi.advanceTimersByTime(1000);
  });
  expect(renders).toBe(40); // each card once, all from the one tick
  const shown = [...view.container.querySelectorAll("span")].map((el) => el.textContent);
  expect(new Set(shown).size).toBe(1); // the same second on every card
  view.rerender(<Cards running={false} />);
  expect(clockTimers()).toBe(0);
  const after = renders;
  act(() => {
    vi.advanceTimersByTime(5000);
  });
  expect(renders).toBe(after); // finished: the labels stay as they were
  expect([...view.container.querySelectorAll("span")].map((el) => el.textContent)).toEqual(shown);
});

it("a clock turned on again reads the time at once", () => {
  function Card({ running }: { running: boolean }) {
    return <span>{useNow(1000, running)}</span>;
  }
  const view = render(<Card running={false} />);
  const first = Number(view.container.textContent);
  act(() => {
    vi.advanceTimersByTime(90_000);
  });
  expect(Number(view.container.textContent)).toBe(first); // off: it kept its value
  view.rerender(<Card running />);
  expect(Number(view.container.textContent)).toBe(first + 90_000);
});
