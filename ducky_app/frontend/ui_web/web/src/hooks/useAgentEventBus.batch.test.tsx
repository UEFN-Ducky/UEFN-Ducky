// @vitest-environment jsdom
import { Profiler, useEffect, useState } from "react";
import { act, render } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { AgentEvent } from "../types/panel";

let frames: FrameRequestCallback[] = [];
function runFrame() {
  const batch = frames;
  frames = [];
  act(() => { for (const cb of batch) cb(performance.now()); });
}

beforeEach(() => {
  vi.resetModules();
  frames = [];
  vi.stubGlobal("requestAnimationFrame", (cb: FrameRequestCallback) => frames.push(cb));
  vi.stubGlobal("fetch", () => new Promise(() => {})); // the bus's event poll never answers here
});
afterEach(() => vi.unstubAllGlobals());

/** 500 deltas from 50 agents, interleaved the way one long-poll batch arrives. */
const interleaved = (): AgentEvent[] =>
  Array.from({ length: 500 }, (_, i) => ({ type: "text_delta", text: `${i} `, conv_id: `c${i % 50}`, run_id: "r" }));

describe("agent event bus batching", () => {
  it("a 500-event long-poll batch is one frame and one React commit", async () => {
    const bus = await import("./useAgentEventBus");
    let commits = 0;
    function Streams() {
      const [text, setText] = useState<Record<string, string>>({});
      useEffect(() => bus.subscribeAgentEvents((e) => setText((t) => ({ ...t, [e.conv_id!]: (t[e.conv_id!] ?? "") + e.text }))), []);
      return <span>{Object.keys(text).length}</span>;
    }
    const { container } = render(<Profiler id="s" onRender={() => commits++}><Streams /></Profiler>);
    commits = 0;
    for (const e of interleaved()) bus.pushLocalAgentEvent(e);
    expect(frames).toHaveLength(1);
    runFrame();
    expect(commits).toBe(1);
    expect(frames).toHaveLength(0); // nothing left for a second frame
    expect(container.textContent).toBe("50");
  });

  it("merges each run's interleaved deltas in order, never across another event of that run", async () => {
    const { coalesceAgentEvents } = await import("./useAgentEventBus");
    const out = coalesceAgentEvents([
      { type: "text_delta", text: "a", conv_id: "c1", run_id: "r" },
      { type: "text_delta", text: "x", conv_id: "c2", run_id: "r" },
      { type: "text_delta", text: "b", conv_id: "c1", run_id: "r" },
      { type: "tool", conv_id: "c2", run_id: "r" },
      { type: "text_delta", text: "y", conv_id: "c2", run_id: "r" },
      { type: "status", text: "one", conv_id: "c1", run_id: "r" },
      { type: "text_delta", text: "z", conv_id: "c2", run_id: "r" },
      { type: "status", text: "two", conv_id: "c1", run_id: "r" },
    ]);
    expect(out.map((e) => `${e.conv_id}:${e.type}:${e.text ?? ""}`)).toEqual([
      "c1:text_delta:ab", "c2:text_delta:x", "c2:tool:", "c2:text_delta:yz", "c1:status:two",
    ]);
  });

  it("a slow frame stops at its time budget and finishes on the next frame", async () => {
    const bus = await import("./useAgentEventBus");
    const heard: string[] = [];
    bus.subscribeAgentEvents((e) => {
      heard.push(e.conv_id!);
      const until = performance.now() + 10;
      while (performance.now() < until) { /* a slow subscriber */ }
    });
    for (let i = 0; i < 3; i++) bus.pushLocalAgentEvent({ type: "tool", conv_id: `c${i}`, run_id: "r" });
    runFrame();
    expect(heard).toEqual(["c0"]);
    runFrame();
    runFrame();
    expect(heard).toEqual(["c0", "c1", "c2"]);
  });
});
