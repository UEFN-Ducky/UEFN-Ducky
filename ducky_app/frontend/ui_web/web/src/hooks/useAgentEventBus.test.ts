import { describe, expect, it } from "vitest";
import type { AgentEvent } from "../types/panel";
import { coalesceAgentEvents } from "./useAgentEventBus";

describe("coalesceAgentEvents", () => {
  it("joins adjacent high-frequency deltas without crossing tool boundaries", () => {
    const events: AgentEvent[] = [
      { type: "thinking", text: "one ", conv_id: "c", run_id: "r" },
      { type: "thinking", text: "two", conv_id: "c", run_id: "r" },
      { type: "tool", text: "read", conv_id: "c", run_id: "r" },
      { type: "text_delta", text: "answer ", conv_id: "c", run_id: "r" },
      { type: "text_delta", text: "text", conv_id: "c", run_id: "r" },
    ];

    expect(coalesceAgentEvents(events)).toEqual([
      { type: "thinking", text: "one two", conv_id: "c", run_id: "r" },
      events[2],
      { type: "text_delta", text: "answer text", conv_id: "c", run_id: "r" },
    ]);
  });

  it("does not merge different runs and keeps only the latest adjacent status", () => {
    const events: AgentEvent[] = [
      { type: "status", text: "starting", conv_id: "c", run_id: "r" },
      { type: "status", text: "working", conv_id: "c", run_id: "r" },
      { type: "thinking", text: "first", conv_id: "c", run_id: "r" },
      { type: "thinking", text: "second", conv_id: "c", run_id: "r2" },
    ];

    expect(coalesceAgentEvents(events)).toEqual([events[1], events[2], events[3]]);
  });
});
