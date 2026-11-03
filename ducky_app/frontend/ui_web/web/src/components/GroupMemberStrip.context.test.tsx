// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { ChatTab, FolderItem, GroupMemberDto } from "../types/panel";

vi.mock("../hooks/usePanelApi", () => ({
  getApi: () => ({}),
}));
vi.mock("../hooks/useAgentEventBus", () => ({
  subscribeAgentEvents: () => () => {},
}));

const { GroupMemberStrip } = await import("./GroupMemberStrip");
const { resetRosterStore } = await import("./team/rosterStore");

afterEach(() => {
  cleanup();
  localStorage.clear();
  resetRosterStore();
});

function folder(partial: Partial<FolderItem> & Pick<FolderItem, "id" | "name">): FolderItem {
  return {
    parentId: "",
    sortOrder: 0,
    expanded: true,
    chats: [],
    children: [],
    ...partial,
  };
}

describe("group roster context", () => {
  it("shows each agent's tokens, including agents inside a nested group", () => {
    const members: GroupMemberDto[] = [
      { member_conv_id: "level", profile_id: "", name: "Level Designer" },
      { member_conv_id: "g1", profile_id: "", name: "Group1", is_group: true },
    ];
    const allChats: ChatTab[] = [
      { id: "level", name: "Level Designer", contextTokens: 0 },
      { id: "painter", name: "Painter", contextTokens: 1200 },
    ];
    const folders = [
      folder({
        id: "art",
        name: "Group1",
        groupHubId: "g1",
        chats: [{ id: "painter", name: "Painter", contextTokens: 1200 }],
      }),
    ];
    render(
      <GroupMemberStrip
        groupId="hub"
        members={members}
        folders={folders}
        allChats={allChats}
        onMembersChange={() => {}}
        onOpenMember={() => {}}
      />,
    );
    // The bar counts every agent, the nested group's too.
    expect(screen.getByText("2 agents · 1 group")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: /^Team/ }));
    expect(screen.getByText("0")).toBeTruthy();
    expect(screen.getByText("Painter")).toBeTruthy();
    // Painter's own row and the nested group's row.
    expect(screen.getAllByText("1.2K")).toHaveLength(2);
    expect(screen.queryByText(/Total context/)).toBeNull();
  });
});
