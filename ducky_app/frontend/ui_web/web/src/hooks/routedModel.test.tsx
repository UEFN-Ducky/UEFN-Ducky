// @vitest-environment jsdom
import { act, cleanup, render, screen } from "@testing-library/react";
import { afterEach, expect, it } from "vitest";

import { AgentActivityPanel } from "../components/AgentActivityPanel";
import { getRoutedModel, routedModelLabel, setRoutedModel } from "./routedModel";

afterEach(cleanup);

it("keeps the last routed model per chat", () => {
  setRoutedModel("chat-a", "gemini:gemini-3.8-flash");
  setRoutedModel("chat-a", "");
  expect(getRoutedModel("chat-a")).toBe("gemini:gemini-3.8-flash");
  expect(getRoutedModel("chat-b")).toBe("");
  expect(routedModelLabel("ollama:llama3:8b")).toBe("llama3:8b");
});

it("names the routed model beside the ducky only for a routed chat", () => {
  const panel = (chatId: string) => (
    <AgentActivityPanel lines={[]} isWaitingOnLinked={false} waitingCount={0} chatId={chatId} showIdleTimer />
  );
  const { rerender } = render(panel("plain-chat"));
  expect(screen.queryByText(/^via /)).toBeNull();

  rerender(panel("auto-chat"));
  act(() => setRoutedModel("auto-chat", "anthropic:claude-sonnet-5-5"));
  expect(screen.getByText("via claude-sonnet-5-5")).toBeTruthy();
});
