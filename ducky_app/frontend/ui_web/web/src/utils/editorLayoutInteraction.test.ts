import { expect, it } from "vitest";
import { activateTab, createDefaultLayout, focusGroup } from "./editorLayoutOps";

it("does not invalidate workspace state when clicking its focused pane or active tab", () => {
  const layout = createDefaultLayout(["one", "two"]);
  const group = layout.groups[layout.focusedGroupId];
  expect(focusGroup(layout, group.id)).toBe(layout);
  expect(activateTab(layout, group.id, group.activeTabId!)).toBe(layout);
  const other = group.tabIds.find((id) => id !== group.activeTabId)!;
  const next = activateTab(layout, group.id, other);
  expect(next).not.toBe(layout);
  expect(next.groups[group.id].activeTabId).toBe(other);
});
