// @vitest-environment jsdom
import { act, cleanup, render } from "@testing-library/react";
import { Profiler } from "react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import type { ChatTab } from "../types/panel";

// Every panel call answers with an empty list: enough for the pane to settle.
const api = vi.hoisted(() => new Proxy({}, {
  get: (_target, prop) => (prop === "then" ? undefined : () => Promise.resolve([])),
}));
vi.mock("../hooks/usePanelApi", () => ({ getApi: () => api, isRemote: () => false, isPanelApiReady: () => true }));
vi.mock("./VirtualChatMessageList", () => ({ VirtualChatMessageList: () => null }));
vi.mock("../voice/VoiceControls", () => ({ VoiceControls: () => null }));
vi.mock("../voice/VoiceOverlay", () => ({ VoiceOverlay: () => null }));
vi.mock("./ModelSelector", () => ({ ModelSelector: () => null }));
vi.mock("./ModeSelector", () => ({ ModeSelector: () => null }));
vi.mock("./DuckyParade", () => ({ DuckyParadeOverlay: () => null }));
vi.mock("./ElapsedTimer", () => ({ ElapsedTimer: () => null }));

import { ChatPane } from "./ChatPane";
import { ConfirmModalProvider } from "../contexts/ConfirmModalContext";
import { markChatTurnIdle, markChatTurnRunning } from "../hooks/chatTurnTimer";

const chat: ChatTab = { id: "turn-timer-chat", name: "Builder" };

beforeEach(() => {
  vi.useFakeTimers();
  vi.stubGlobal("matchMedia", () => ({ matches: false, addEventListener() {}, removeEventListener() {} }));
  markChatTurnRunning(chat.id);
});

afterEach(() => {
  markChatTurnIdle(chat.id);
  cleanup();
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

it("does not re-render the whole chat pane on every tick of a running turn", async () => {
  let commits = 0;
  render(
    <ConfirmModalProvider>
      <Profiler id="pane" onRender={() => { commits += 1; }}>
        <ChatPane chat={chat} visible={false} allChats={[chat]} onOpenChat={() => {}} isAgentRunning />
      </Profiler>
    </ConfirmModalProvider>,
  );
  await act(async () => { await vi.advanceTimersByTimeAsync(3000); });
  const commitsBefore = commits;

  for (let i = 0; i < 8; i++) await act(async () => { await vi.advanceTimersByTimeAsync(250); });

  expect(commits).toBe(commitsBefore);

  // The pane still hears the turn end (it shows the finished time in its footer).
  act(() => markChatTurnIdle(chat.id));
  expect(commits).toBeGreaterThan(commitsBefore);
});
