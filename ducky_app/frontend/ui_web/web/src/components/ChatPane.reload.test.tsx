// @vitest-environment jsdom
import { expect, it } from "vitest";
import type { ChatTab } from "../types/panel";
import { samePaneProps } from "./ChatPane";

const noop = () => {};
const a: ChatTab = { id: "a", name: "A" };
const b: ChatTab = { id: "b", name: "B" };
const bChild: ChatTab = { id: "b1", name: "Helper", parentConvId: "b" };
const props = (chat: ChatTab, allChats: ChatTab[], extra: object = {}) => ({
  chat, visible: true, allChats, folders: [], onOpenChat: noop, isAgentRunning: false, ...extra,
});

it("a rename of chat A does not render chat B's pane", () => {
  const before = [a, b, bChild];
  const after = [{ ...a, name: "A renamed" }, b, bChild];
  expect(samePaneProps(props(b, before), props(b, after))).toBe(true);
  // A's own pane does render.
  expect(samePaneProps(props(a, before), props(after[0], after))).toBe(false);
});

it("B's pane renders when its linked child changes or its agent starts", () => {
  const before = [a, b, bChild];
  expect(samePaneProps(props(b, before), props(b, [a, b, { ...bChild, name: "Renamed" }]))).toBe(false);
  expect(samePaneProps(props(b, before), props(b, [a, b]))).toBe(false);
  expect(samePaneProps(props(b, before), props(b, before, { isAgentRunning: true }))).toBe(false);
});

it("a copy of the same chat row and a fresh onEngage lambda count as the same", () => {
  const all = [a, b];
  expect(samePaneProps(props(b, all, { onEngage: () => {} }), props({ ...b }, all, { onEngage: () => {} }))).toBe(true);
  expect(samePaneProps(props(b, all, { onEngage: () => {} }), props(b, all))).toBe(false);
});

it("group panes compare the whole chat list (they list every chat to invite)", () => {
  const hub: ChatTab = { id: "g", name: "Team", isGroup: true };
  expect(samePaneProps(props(hub, [a, hub]), props(hub, [{ ...a, name: "A2" }, hub]))).toBe(false);
});
