// @vitest-environment jsdom
/**
 * Long-chat render budget: a 2,000-message chat with the REAL row components.
 * Records first render time, rows mounted, and rows re-rendered per streamed
 * token. Each row component is wrapped (not replaced) by a render counter.
 */
import { act, cleanup, render } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import {
  appendStreamRow,
  buildChatRows,
  coalesceActivityRows,
  type ChatRow,
} from "../utils/chatMessageGroups";
import { syntheticMessages } from "./syntheticChat";

const probes = vi.hoisted(() => ({ renders: 0 }));

vi.mock("../components/MessageBubble", async (importOriginal) => {
  const real = await importOriginal<typeof import("../components/MessageBubble")>();
  const Real = real.MessageBubble;
  return {
    ...real,
    MessageBubble: (p: Parameters<typeof Real>[0]) => {
      probes.renders++;
      return <Real {...p} />;
    },
  };
});
vi.mock("../components/EditableUserMessage", async (importOriginal) => {
  const real = await importOriginal<typeof import("../components/EditableUserMessage")>();
  const Real = real.EditableUserMessage;
  return {
    ...real,
    EditableUserMessage: (p: Parameters<typeof Real>[0]) => {
      probes.renders++;
      return <Real {...p} />;
    },
  };
});
vi.mock("../components/ToolExecutionCard", async (importOriginal) => {
  const real = await importOriginal<typeof import("../components/ToolExecutionCard")>();
  const Real = real.ToolExecutionCard;
  return {
    ...real,
    ToolExecutionCard: (p: Parameters<typeof Real>[0]) => {
      probes.renders++;
      return <Real {...p} />;
    },
  };
});
vi.mock("../components/AgentActivityGroup", async (importOriginal) => {
  const real = await importOriginal<typeof import("../components/AgentActivityGroup")>();
  const Real = real.AgentActivityGroup;
  return {
    ...real,
    AgentActivityGroup: (p: Parameters<typeof Real>[0]) => {
      probes.renders++;
      return <Real {...p} />;
    },
  };
});

import { VirtualChatMessageList } from "../components/VirtualChatMessageList";

const noop = () => {};
const EMPTY: never[] = [];
const MESSAGES = 2000;
const TOKENS = 200;

function props(rows: ChatRow[]) {
  return {
    rows,
    showActivityPanel: false,
    activityHeaderOnly: false,
    isWaitingOnLinked: false,
    waitingLinked: EMPTY,
    isAtBottom: true,
    hasNewBelow: false,
    editableRowId: null,
    composerMode: "agent" as const,
    composerModel: "perf",
    setComposerModel: noop,
    composerCodingAgent: "ducky",
    setComposerCodingAgent: noop,
    convId: "perf",
    onResend: noop,
    onAtBottomChange: noop,
    onJumpToLatest: noop,
    onOpenChat: noop,
    onStopLinked: noop,
    allChats: EMPTY,
    linkedAgents: EMPTY,
    activityLines: EMPTY,
    chatPlan: null,
    chatPlanProgress: null,
    planAllDone: false,
  };
}

afterEach(cleanup);

describe("chat list perf: 2,000 messages", () => {
  it("first render is windowed and a streamed token re-renders <= 2 rows", () => {
    // syntheticMessages: 8 messages per turn (user, 3 tool calls + 3 results, answer).
    const messages = syntheticMessages(MESSAGES / 8);
    expect(messages).toHaveLength(MESSAGES);
    const committed = buildChatRows({ committed: messages, turnMessages: [], streamBuffer: "", inFlight: false });

    probes.renders = 0;
    const t0 = performance.now();
    const view = render(<VirtualChatMessageList {...props(committed)} />);
    const firstRenderMs = performance.now() - t0;
    const firstRenderRows = probes.renders;
    const mountedTurns = view.container.querySelectorAll(".virtual-chat-turn").length;
    const domNodes = view.container.querySelectorAll("*").length;

    let text = "";
    const perToken: number[] = [];
    const commitMs: number[] = [];
    for (let i = 0; i < TOKENS; i++) {
      text += "streamed token ";
      const rows = coalesceActivityRows(appendStreamRow(committed, text, true));
      probes.renders = 0;
      const c0 = performance.now();
      act(() => view.rerender(<VirtualChatMessageList {...props(rows)} />));
      commitMs.push(performance.now() - c0);
      perToken.push(probes.renders);
    }
    const avg = (a: number[]) => a.reduce((s, v) => s + v, 0) / a.length;
    const stats = {
      messages: MESSAGES,
      rows: committed.length,
      firstRenderMs: +firstRenderMs.toFixed(1),
      firstRenderRows,
      mountedTurns,
      domNodes,
      tokens: TOKENS,
      rerendersPerTokenAvg: +avg(perToken.slice(1)).toFixed(2),
      rerendersPerTokenMax: Math.max(...perToken.slice(1)),
      commitAvgMs: +avg(commitMs).toFixed(2),
      commitMaxMs: +Math.max(...commitMs).toFixed(2),
    };
    console.log(`CHAT_PERF ${JSON.stringify(stats)}`);

    // Budgets: only the tail is mounted, and a token touches only the streaming row.
    expect(mountedTurns).toBeLessThanOrEqual(4);
    expect(stats.rerendersPerTokenMax).toBeLessThanOrEqual(2);
    // Its budgets are counts, not times: building 2,000 messages and streaming TOKENS rerenders
    // can pass vitest's 5 s default on a loaded PC (a release run), which says nothing about them.
  }, 60_000);
});
