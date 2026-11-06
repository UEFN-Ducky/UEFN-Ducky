// @vitest-environment jsdom
import { act, render, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { pushLocalAgentEvent } from "../../hooks/useAgentEventBus";
import type { AgentEvent } from "../../types/panel";

const listAll = vi.fn();
vi.mock("../../hooks/usePanelApi", () => ({
  getApi: () => ({ list_all_conversations: listAll }),
  isRemote: () => false,
}));
vi.mock("../changes/ChangesView", () => ({
  ChangesView: ({ allChats }: { allChats: Array<{ id: string }> }) => (
    <div data-testid="chats">{allChats.map((c) => c.id).join(",")}</div>
  ),
}));

beforeEach(() => {
  vi.stubGlobal("fetch", vi.fn(() => new Promise(() => undefined)));
});

afterEach(() => {
  vi.unstubAllGlobals();
  listAll.mockReset();
});

it("re-reads the chat list on chats_changed (a cached start-up list is replaced)", async () => {
  listAll.mockResolvedValueOnce([{ id: "old", profile_id: "p1" }]);
  listAll.mockResolvedValue([{ id: "old", profile_id: "p1" }, { id: "new", profile_id: "p1" }, { id: "x", profile_id: "p2" }]);
  const { DuckyProfileLedger } = await import("./DuckyProfileLedger");
  const view = render(<DuckyProfileLedger profileId="p1" profileName="Dev" />);
  await waitFor(() => expect(view.getByTestId("chats").textContent).toBe("old"));

  act(() => pushLocalAgentEvent({ type: "chats_changed" } as AgentEvent));
  await waitFor(() => expect(view.getByTestId("chats").textContent).toBe("old,new"), { timeout: 3000 });
  expect(listAll).toHaveBeenCalledTimes(2);
  view.unmount();
});
