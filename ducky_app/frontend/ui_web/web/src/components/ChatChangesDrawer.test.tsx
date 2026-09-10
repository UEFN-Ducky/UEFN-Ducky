// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../hooks/usePanelApi", () => ({
  getApi: () => ({ list_changesets: vi.fn().mockResolvedValue([]) }),
}));
vi.mock("../hooks/useAgentEventBus", () => ({
  subscribeAgentEvents: () => () => {},
}));
vi.mock("../hooks/useRunningAgents", () => ({
  useRunningAgents: () => new Set<string>(),
}));
vi.mock("../contexts/ConfirmModalContext", () => ({
  useConfirmModal: () => ({ confirm: async () => true }),
}));

const { ChatChangesButton, ChatChangesSlide, changesMaxHeight, getLedgerOpen, resetLedgerOpen, useLedgerOpen } =
  await import("./ChatChangesDrawer");

beforeEach(() => {
  vi.stubGlobal(
    "ResizeObserver",
    class {
      observe() {}
      disconnect() {}
    },
  );
});

afterEach(() => {
  cleanup();
  resetLedgerOpen();
  vi.unstubAllGlobals();
});

function LedgerProbe() {
  const [open, setOpen] = useLedgerOpen();
  return (
    <button type="button" onClick={() => setOpen((v) => !v)}>
      {open ? "open" : "closed"}
    </button>
  );
}

describe("ledger open state", () => {
  it("stays open after the chat pane remounts", () => {
    const first = render(<LedgerProbe />);
    expect(first.getByText("closed")).toBeTruthy();
    fireEvent.click(first.getByRole("button"));
    expect(getLedgerOpen()).toBe(true);
    first.unmount();
    const second = render(<LedgerProbe />);
    expect(second.getByText("open")).toBeTruthy();
  });
});

describe("ChatChangesButton", () => {
  it("toggles pressed state for this chat's history drawer", () => {
    const onClick = vi.fn();
    const { rerender } = render(<ChatChangesButton open={false} onClick={onClick} />);
    const btn = screen.getByTitle(/this chat's ledger/i);
    expect(btn.getAttribute("aria-expanded")).toBe("false");
    fireEvent.click(btn);
    expect(onClick).toHaveBeenCalledOnce();
    rerender(<ChatChangesButton open onClick={onClick} />);
    expect(screen.getByTitle(/this chat's ledger/i).getAttribute("aria-expanded")).toBe("true");
  });
});

describe("ChatChangesSlide", () => {
  it("puts this chat's full ledger here, with a drag handle and no project-wide link", async () => {
    const host = document.createElement("div");
    Object.defineProperty(host, "clientHeight", { value: 800 });
    render(<ChatChangesSlide open convId="chat-1" host={host} />);
    expect(screen.getByLabelText("Resize this chat's ledger")).toBeTruthy();
    expect(screen.queryByText(/See all changes/i)).toBeNull();
    await waitFor(() => expect(screen.getByLabelText("Filter by kind")).toBeTruthy());
    expect(screen.queryByLabelText("Filter by time")).toBeNull();
    expect(screen.getByLabelText("Search ledger")).toBeTruthy();
    expect(screen.queryByLabelText("Filter by ducky")).toBeNull();
  });

  it("caps a full-height sheet so the composer still has room", () => {
    const host = document.createElement("div");
    Object.defineProperty(host, "clientHeight", { value: 800 });
    const box = document.createElement("div");
    box.className = "chat-pane-input-box";
    Object.defineProperty(box, "offsetHeight", { value: 160 });
    host.appendChild(box);
    expect(changesMaxHeight(host)).toBe(800 - 160);
  });

  it("ignores the ledger's own height when it lives inside the composer", () => {
    const host = document.createElement("div");
    Object.defineProperty(host, "clientHeight", { value: 800 });
    const box = document.createElement("div");
    box.className = "chat-pane-input-box";
    Object.defineProperty(box, "offsetHeight", { value: 440 });
    const ledger = document.createElement("div");
    ledger.className = "chat-changes-host";
    Object.defineProperty(ledger, "offsetHeight", { value: 280 });
    box.appendChild(ledger);
    host.appendChild(box);
    expect(changesMaxHeight(host)).toBe(800 - 160);
  });

  it("closes from the top-right X", () => {
    const onClose = vi.fn();
    const host = document.createElement("div");
    Object.defineProperty(host, "clientHeight", { value: 800 });
    render(<ChatChangesSlide open convId="chat-1" host={host} onClose={onClose} />);
    fireEvent.click(screen.getByRole("button", { name: "Close ledger" }));
    expect(onClose).toHaveBeenCalledOnce();
  });
});
