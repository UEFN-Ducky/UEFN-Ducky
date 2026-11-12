// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from "vitest";

const openChat = vi.fn((_id: string) => true);
const openSettings = vi.fn();
let pushed: ((event: Record<string, unknown>) => void) | null = null;

vi.mock("../hooks/usePanelApi", () => ({ isRemote: () => false }));
vi.mock("../hooks/usePanelPushBus", () => ({
  subscribePanelPush: (fn: (event: Record<string, unknown>) => void) => {
    pushed = fn;
    return () => {
      pushed = null;
    };
  },
}));
vi.mock("../navigation/openChatReference", () => ({ requestOpenChatTab: (id: string) => openChat(id) }));
vi.mock("../hooks/graphActivity", () => ({ requestFocusGraph: vi.fn() }));
vi.mock("../navigation/openWorkflowsTab", () => ({ requestOpenWorkflowsTab: vi.fn() }));
vi.mock("../navigation/openSettingsTab", () => ({ requestOpenSettings: (tab: string) => openSettings(tab) }));

import { installOpenFromNotification } from "./openFromParent";

afterEach(() => {
  openChat.mockClear();
  openSettings.mockClear();
});

describe("a clicked Windows notification", () => {
  it("opens the chat it names", () => {
    const stop = installOpenFromNotification();
    pushed?.({ type: "open_target", kind: "chat", id: "c1" });
    expect(openChat).toHaveBeenCalledWith("c1");
    expect(openSettings).not.toHaveBeenCalled();
    stop();
  });

  it("opens Settings > General for a PC memory warning, and no other tab", () => {
    const stop = installOpenFromNotification();
    pushed?.({ type: "open_target", kind: "settings", id: "General" });
    expect(openSettings).toHaveBeenCalledWith("General");
    pushed?.({ type: "open_target", kind: "settings", id: "Account" });
    expect(openSettings).toHaveBeenCalledTimes(1);
    expect(openChat).not.toHaveBeenCalled();
    stop();
  });
});
