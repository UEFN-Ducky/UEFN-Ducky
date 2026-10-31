// @vitest-environment jsdom
import { expect, it, vi } from "vitest";

const opened = vi.hoisted(() => vi.fn());
vi.mock("./openPluginUiTab", () => ({ requestOpenPluginUiTab: opened }));

import { requestOpenChatReference } from "../navigation/openChatReference";
import { panelItemKey, subscribePanelItems, takePanelItem } from "./panelOpenItem";

it("an item reference opens the panel and leaves the item for it, once", () => {
  const heard: string[] = [];
  const stop = subscribePanelItems((key) => heard.push(key));
  requestOpenChatReference("plugin:kanban/board:b123", "/Limo backlog");
  stop();
  expect(opened).toHaveBeenCalledWith("kanban", "board", undefined);
  expect(heard).toEqual([panelItemKey("kanban", "board")]);
  expect(takePanelItem(panelItemKey("Kanban", "Board"))).toBe("b123");
  expect(takePanelItem(panelItemKey("kanban", "board"))).toBeNull();
});

it("a plain panel reference still opens the panel with its name", () => {
  requestOpenChatReference("plugin:kanban/board", "/Kanban");
  expect(opened).toHaveBeenLastCalledWith("kanban", "board", "Kanban");
  expect(takePanelItem(panelItemKey("kanban", "board"))).toBeNull();
});
