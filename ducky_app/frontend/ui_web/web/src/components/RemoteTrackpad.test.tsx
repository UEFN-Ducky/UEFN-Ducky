// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

const { sent, dictation } = vi.hoisted(() => ({
  sent: [] as Record<string, unknown>[],
  dictation: {
    opts: null as null | { onFinal: (words: string) => void; onInterim: (words: string) => void },
    calls: [] as string[],
    finishStart: () => {},
    startDelayed: false,
  },
}));

vi.mock("../voice/useDictation", () => ({
  useDictation: (opts: typeof dictation.opts) => {
    dictation.opts = opts;
    return {
      status: "idle",
      notice: null,
      isRecording: false,
      start: () => {
        dictation.calls.push("start");
        return dictation.startDelayed
          ? new Promise<void>((resolve) => {
              dictation.finishStart = resolve;
            })
          : Promise.resolve();
      },
      stop: async () => void dictation.calls.push("stop"),
      abort: () => void dictation.calls.push("abort"),
    };
  },
}));

vi.mock("../remote/directTransport", () => ({
  getDirectTransport: () => ({
    sendInput: (payload: Record<string, unknown>) => {
      sent.push(payload);
      return true;
    },
    onStatus: (fn: (s: { state: string }) => void) => {
      fn({ state: "live" });
      return () => {};
    },
  }),
}));

import { RemoteTrackpad } from "./RemoteTrackpad";

const wait = (ms: number) => act(() => new Promise<void>((resolve) => window.setTimeout(resolve, ms)));

function motionEvent(alpha: number) {
  const event = new Event("devicemotion");
  Object.assign(event, { rotationRate: { alpha, beta: 0, gamma: 0 }, accelerationIncludingGravity: { x: 0, y: 0, z: 9.8 } });
  act(() => void window.dispatchEvent(event));
}

beforeEach(() => {
  sent.length = 0;
  dictation.calls.length = 0;
  dictation.startDelayed = false;
  localStorage.clear();
  class TestPointerEvent extends MouseEvent {
    pointerId: number;
    constructor(type: string, init: PointerEventInit = {}) {
      super(type, init);
      this.pointerId = init.pointerId ?? 1;
    }
  }
  window.PointerEvent = TestPointerEvent as unknown as typeof PointerEvent;
});

afterEach(cleanup);

it("a tap is a left click at the cursor", async () => {
  render(<RemoteTrackpad />);
  const pad = screen.getByTestId("trackpad-pad");
  fireEvent.pointerDown(pad, { clientX: 10, clientY: 10 });
  fireEvent.pointerUp(pad, { clientX: 10, clientY: 10 });
  await wait(90);
  expect(sent).toEqual([
    { view: "desktop", type: "down", button: 0 },
    { view: "desktop", type: "up", button: 0 },
  ]);
});

it("a slide sends a relative move and no click", async () => {
  render(<RemoteTrackpad />);
  const pad = screen.getByTestId("trackpad-pad");
  fireEvent.pointerDown(pad, { clientX: 0, clientY: 0 });
  fireEvent.pointerMove(pad, { clientX: 20, clientY: 0 });
  fireEvent.pointerUp(pad, { clientX: 20, clientY: 0 });
  await wait(90);
  expect(sent).toEqual([{ view: "desktop", type: "move", dx: 40, dy: 0, pad: true }]);
});

it("a hold presses the button and lifting drops it", async () => {
  render(<RemoteTrackpad />);
  const pad = screen.getByTestId("trackpad-pad");
  fireEvent.pointerDown(pad, { clientX: 0, clientY: 0 });
  await wait(400);
  expect(sent).toEqual([{ view: "desktop", type: "down", button: 0 }]);
  fireEvent.pointerUp(pad, { clientX: 0, clientY: 0 });
  expect(sent[1]).toEqual({ view: "desktop", type: "up", button: 0 });
});

it("pausing mid-drag lets go of the button, then sends nothing", async () => {
  render(<RemoteTrackpad />);
  fireEvent.pointerDown(screen.getByTestId("trackpad-pad"), { clientX: 0, clientY: 0 });
  await wait(400);
  fireEvent.click(screen.getByText("Pause"));
  expect(sent.at(-1)).toEqual({ view: "desktop", type: "up", button: 0 });
  const count = sent.length;
  fireEvent.click(screen.getByText("Right click"));
  await wait(90);
  expect(sent).toHaveLength(count);
});

it("right click, Center, and the scroll strip send the right events", async () => {
  render(<RemoteTrackpad />);
  fireEvent.click(screen.getByText("Right click"));
  await wait(90);
  expect(sent).toEqual([
    { view: "desktop", type: "down", button: 2 },
    { view: "desktop", type: "up", button: 2 },
  ]);
  sent.length = 0;
  fireEvent.click(screen.getByText("Center"));
  expect(sent).toEqual([{ view: "monitor:0", type: "move", x: 0.5, y: 0.5 }]);
  sent.length = 0;
  const strip = screen.getByTestId("trackpad-scroll");
  fireEvent.pointerDown(strip, { clientY: 0 });
  fireEvent.pointerMove(strip, { clientY: 50 });
  expect(sent).toEqual([{ view: "desktop", type: "wheel", delta: 2 }]);
});

it("Motion moves the cursor from the gyro, only while it is on", async () => {
  render(<RemoteTrackpad />);
  motionEvent(-100);
  await wait(40);
  motionEvent(-100);
  await wait(40);
  expect(sent).toEqual([]);
  await act(async () => void fireEvent.click(screen.getByText("Motion")));
  motionEvent(-100);
  await wait(40);
  motionEvent(-100);
  await wait(40);
  expect(sent).toHaveLength(1);
  expect(sent[0]).toMatchObject({ view: "desktop", type: "move", dy: 0 });
  expect(sent[0].dx as number).toBeGreaterThan(0);
});

it("holding talk records, letting go stops, and the words are typed at the PC's cursor", async () => {
  render(<RemoteTrackpad />);
  const talk = screen.getByLabelText("Hold to talk");
  fireEvent.pointerDown(talk);
  expect(dictation.calls).toEqual(["start"]);
  fireEvent.pointerUp(talk);
  await wait(0);
  expect(dictation.calls).toEqual(["start", "stop"]);
  act(() => dictation.opts?.onFinal("hello there"));
  expect(sent).toEqual([{ view: "desktop", type: "text", text: "hello there " }]);
});

it("letting go while the mic is still starting ends it once started", async () => {
  dictation.startDelayed = true;
  render(<RemoteTrackpad />);
  const talk = screen.getByLabelText("Hold to talk");
  fireEvent.pointerDown(talk);
  fireEvent.pointerUp(talk);
  await wait(0);
  expect(dictation.calls).toEqual(["start"]);
  dictation.finishStart();
  await wait(0);
  expect(dictation.calls).toEqual(["start", "stop"]);
});

it("talking freezes the air mouse", async () => {
  render(<RemoteTrackpad />);
  await act(async () => void fireEvent.click(screen.getByText("Motion")));
  await wait(240);
  fireEvent.pointerDown(screen.getByLabelText("Hold to talk"));
  await wait(240);
  motionEvent(-100);
  await wait(40);
  motionEvent(-100);
  await wait(40);
  expect(sent).toEqual([]);
});

it("pause cancels a recording and its words are not typed", () => {
  render(<RemoteTrackpad />);
  fireEvent.pointerDown(screen.getByLabelText("Hold to talk"));
  fireEvent.click(screen.getByText("Pause"));
  expect(dictation.calls).toEqual(["start", "abort"]);
  act(() => dictation.opts?.onFinal("too late"));
  expect(sent).toEqual([]);
});

function openController() {
  HTMLElement.prototype.setPointerCapture = () => {};
  HTMLElement.prototype.hasPointerCapture = () => true;
  vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockReturnValue({
    left: 0, top: 0, width: 100, height: 100, right: 100, bottom: 100, x: 0, y: 0, toJSON: () => ({}),
  } as DOMRect);
  render(<RemoteTrackpad />);
  fireEvent.click(screen.getByRole("tab", { name: "Controller" }));
}

it("the Controller tab swaps the trackpad for two sticks, and back", () => {
  openController();
  expect(screen.queryByTestId("trackpad-pad")).toBeNull();
  expect(screen.getByTestId("controller-move")).toBeTruthy();
  expect(screen.getByTestId("controller-look")).toBeTruthy();
  fireEvent.click(screen.getByRole("tab", { name: "Mouse" }));
  expect(screen.getByTestId("trackpad-pad")).toBeTruthy();
  expect(screen.queryByTestId("controller-move")).toBeNull();
  vi.restoreAllMocks();
});

it("the left stick holds movement keys and lets them go", () => {
  openController();
  const stick = screen.getByTestId("controller-move");
  fireEvent.pointerDown(stick, { clientX: 100, clientY: 50 });
  expect(sent).toEqual([{ view: "desktop", type: "keydown", key: "d" }]);
  sent.length = 0;
  fireEvent.pointerUp(stick, { clientX: 100, clientY: 50 });
  expect(sent).toEqual([{ view: "desktop", type: "keyup", key: "d" }]);
  vi.restoreAllMocks();
});

it("the right stick looks with plain mouse movement and never presses a button", async () => {
  openController();
  fireEvent.pointerDown(screen.getByTestId("controller-look"), { clientX: 100, clientY: 50 });
  await wait(60);
  expect(sent.length).toBeGreaterThan(0);
  expect(sent.every((event) => event.type === "move" && (event.dx as number) > 0 && !("pad" in event))).toBe(true);
  vi.restoreAllMocks();
});

it("Fire and Jump hold while pressed", () => {
  openController();
  fireEvent.pointerDown(screen.getByText("Fire"));
  expect(sent).toEqual([{ view: "desktop", type: "down", button: 0 }]);
  fireEvent.pointerUp(screen.getByText("Fire"));
  expect(sent[1]).toEqual({ view: "desktop", type: "up", button: 0 });
  sent.length = 0;
  fireEvent.pointerDown(screen.getByText("Jump"));
  expect(sent).toEqual([{ view: "desktop", type: "keydown", key: " " }]);
  fireEvent.pointerUp(screen.getByText("Jump"));
  vi.restoreAllMocks();
});

it("leaving the controller releases everything still held", () => {
  openController();
  fireEvent.pointerDown(screen.getByTestId("controller-move"), { clientX: 50, clientY: 0 });
  fireEvent.pointerDown(screen.getByText("Aim"));
  sent.length = 0;
  fireEvent.click(screen.getByRole("tab", { name: "Mouse" }));
  expect(sent).toContainEqual({ view: "desktop", type: "keyup", key: "w" });
  expect(sent).toContainEqual({ view: "desktop", type: "up", button: 2 });
  vi.restoreAllMocks();
});

it("the air mouse stays still on the Controller tab", async () => {
  openController();
  fireEvent.click(screen.getByRole("tab", { name: "Mouse" }));
  await act(async () => void fireEvent.click(screen.getByText("Motion")));
  fireEvent.click(screen.getByRole("tab", { name: "Controller" }));
  motionEvent(-100);
  await wait(40);
  motionEvent(-100);
  await wait(40);
  expect(sent).toEqual([]);
  vi.restoreAllMocks();
});

it("the keyboard sends typed text and named keys", async () => {
  render(<RemoteTrackpad />);
  fireEvent.click(screen.getByText("Keyboard"));
  const box = screen.getByLabelText("Text to type on the PC");
  fireEvent.change(box, { target: { value: "hi" } });
  fireEvent.submit(box);
  expect(sent).toEqual([{ view: "desktop", type: "text", text: "hi" }]);
  sent.length = 0;
  fireEvent.click(screen.getByText("Esc"));
  await wait(90);
  expect(sent).toEqual([
    { view: "desktop", type: "keydown", key: "Escape" },
    { view: "desktop", type: "keyup", key: "Escape" },
  ]);
});
