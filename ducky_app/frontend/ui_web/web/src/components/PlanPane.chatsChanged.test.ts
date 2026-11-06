import { expect, it } from "vitest";
import source from "./PlanPane.tsx?raw";

// PlanPane is too big to mount here; its chat list is the Send menu's, read on open.
it("the open Send menu re-reads the chat list on chats_changed", () => {
  const hook = source.slice(source.indexOf("useChatsChanged(async"));
  expect(hook.length).toBeGreaterThan(0);
  expect(hook.slice(0, 120)).toMatch(/if \(sendOpen\) await refreshChats\(\)/);
  expect(source).toMatch(/import \{ useChatsChanged \} from "\.\.\/hooks\/useChatsChanged"/);
});
