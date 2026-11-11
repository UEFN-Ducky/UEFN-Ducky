// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { LocalChatRow, PanelPushEvent } from "../../types/panel";

const api = {
  list_local_chats: vi.fn(),
  adopt_local_chats: vi.fn(),
  delete_local_chats: vi.fn(),
};
const confirm = vi.fn();
const alert = vi.fn();
let push: ((event: PanelPushEvent) => void) | null = null;

vi.mock("../../hooks/usePanelApi", () => ({ getApi: () => api }));
vi.mock("../../contexts/ConfirmModalContext", () => ({ useConfirmModal: () => ({ confirm, alert }) }));
let chatsChanged: (() => Promise<void>) | null = null;
vi.mock("../../hooks/useChatsChanged", () => ({
  useChatsChanged: (load: () => Promise<void>) => {
    chatsChanged = load;
  },
}));
vi.mock("../../hooks/usePanelPushBus", () => ({
  subscribePanelPush: (listener: (event: PanelPushEvent) => void) => {
    push = listener;
    return () => {
      push = null;
    };
  },
}));

import { LocalChats, localChatMeta } from "./LocalChats";

const chat = (id: string, more: Partial<LocalChatRow> = {}): LocalChatRow => ({
  id,
  title: `Chat ${id}`,
  updated: 0,
  chats: 1,
  group: false,
  ...more,
});

async function shown(rows: LocalChatRow[], onOpenChat = vi.fn()) {
  api.list_local_chats.mockResolvedValue({ ok: true, chats: rows });
  const view = render(<LocalChats onOpenChat={onOpenChat} />);
  await act(async () => {});
  return { ...view, onOpenChat };
}

beforeEach(() => {
  vi.clearAllMocks();
  window.localStorage.clear();
  confirm.mockResolvedValue(true);
  alert.mockResolvedValue(undefined);
});

afterEach(cleanup);

describe("localChatMeta", () => {
  it("counts what moves with a group, else says when the chat was last used", () => {
    expect(localChatMeta(chat("g", { group: true, chats: 4 }))).toBe("4 chats");
    expect(localChatMeta(chat("p", { chats: 2 }))).toBe("2 chats");
    expect(localChatMeta(chat("c", { updated: 1_000_000 - 7200 }), 1_000_000)).toBe("2 h ago");
    expect(localChatMeta(chat("c"))).toBe("");
  });
});

describe("LocalChats", () => {
  it("is not there at all while nothing was made signed out, or the list can't be read", async () => {
    const { container } = await shown([]);
    expect(container.innerHTML).toBe("");
    cleanup();
    api.list_local_chats.mockRejectedValue(new Error("offline"));
    const view = render(<LocalChats onOpenChat={vi.fn()} />);
    await act(async () => {});
    expect(view.container.innerHTML).toBe("");
  });

  it("lists them, and opening one adds it to the account and opens it", async () => {
    api.adopt_local_chats.mockResolvedValue({ ok: true, moved: ["a", "a-child"], failed: [] });
    const { onOpenChat } = await shown([chat("a", { title: "Harbour notes" }), chat("b", { title: "" })]);
    expect(screen.getByText("Not in your account")).toBeTruthy();
    expect(screen.getByText("2")).toBeTruthy();
    expect(screen.getByText("Untitled chat")).toBeTruthy();

    api.list_local_chats.mockResolvedValue({ ok: true, chats: [chat("b", { title: "" })] });
    await act(async () => {
      fireEvent.click(screen.getByText("Harbour notes"));
    });
    expect(api.adopt_local_chats).toHaveBeenCalledWith(["a"]);
    expect(onOpenChat).toHaveBeenCalledWith({ id: "a", name: "Harbour notes" });
    expect(screen.queryByText("Harbour notes")).toBeNull();
    expect(alert).not.toHaveBeenCalled();
  });

  it("one that could not move stays listed and says so; nothing opens", async () => {
    api.adopt_local_chats.mockResolvedValue({ ok: false, error: "Your account's data key isn't on this PC yet.", moved: [] });
    const { onOpenChat } = await shown([chat("a")]);
    await act(async () => {
      fireEvent.click(screen.getByText("Chat a"));
    });
    expect(alert).toHaveBeenCalledWith(expect.objectContaining({ message: "Your account's data key isn't on this PC yet." }));
    expect(onOpenChat).not.toHaveBeenCalled();
    expect(screen.getByText("Chat a")).toBeTruthy();
  });

  it("goes away once the last one is added, deleted or the person signs out", async () => {
    const { container } = await shown([chat("a")]);
    expect(container.innerHTML).not.toBe("");
    api.list_local_chats.mockResolvedValue({ ok: true, chats: [] });
    await act(async () => {
      push?.({ type: "duckyos_account_changed" } as PanelPushEvent);
    });
    await waitFor(() => expect(container.innerHTML).toBe(""));
  });

  it("follows the chats: one added or deleted from another window leaves the list", async () => {
    await shown([chat("a"), chat("b")]);
    api.list_local_chats.mockResolvedValue({ ok: true, chats: [chat("b")] });
    await act(async () => {
      await chatsChanged?.();
    });
    expect(screen.queryByText("Chat a")).toBeNull();
    expect(screen.getByText("Chat b")).toBeTruthy();
  });

  it("Add all asks first, then moves every one", async () => {
    api.adopt_local_chats.mockResolvedValue({ ok: true, moved: ["a", "b"], failed: [] });
    await shown([chat("a"), chat("b")]);
    confirm.mockResolvedValueOnce(false);
    await act(async () => {
      fireEvent.click(screen.getByLabelText("Add all to your account"));
    });
    expect(api.adopt_local_chats).not.toHaveBeenCalled();
    await act(async () => {
      fireEvent.click(screen.getByLabelText("Add all to your account"));
    });
    expect(api.adopt_local_chats).toHaveBeenCalledWith(null);
  });

  it("deleting one asks first and names what goes with it", async () => {
    api.delete_local_chats.mockResolvedValue({ ok: true, deleted: ["g", "m1", "m2"] });
    await shown([chat("g", { title: "Build team", group: true, chats: 3 })]);
    fireEvent.contextMenu(screen.getByText("Build team"));
    await act(async () => {
      fireEvent.click(screen.getByText("Delete permanently"));
    });
    expect(confirm).toHaveBeenCalledWith(
      expect.objectContaining({ danger: true, message: 'Permanently delete "Build team" and the 2 with it? This cannot be undone.' }),
    );
    expect(api.delete_local_chats).toHaveBeenCalledWith(["g"]);
  });

  it("remembers that it was folded", async () => {
    await shown([chat("a")]);
    fireEvent.click(screen.getByText("Not in your account"));
    expect(window.localStorage.getItem("ducky.localChats.collapsed")).toBe("1");
    cleanup();
    await shown([chat("a")]);
    expect(screen.getByRole("button", { name: /Not in your account/ }).getAttribute("aria-expanded")).toBe("false");
  });
});
