import { describe, expect, it } from "vitest";

import {
  getLiveVoiceChatIds,
  patchLiveVoiceState,
  setLiveVoiceChat,
  subscribeLiveVoiceChats,
} from "./liveChats";

describe("live chat presence", () => {
  it("notifies subscribers when a chat goes live and off", () => {
    const seen: number[] = [];
    const unsub = subscribeLiveVoiceChats(() => seen.push(getLiveVoiceChatIds().size));
    setLiveVoiceChat("c1", true);
    expect(getLiveVoiceChatIds().has("c1")).toBe(true);
    setLiveVoiceChat("c1", false);
    expect(getLiveVoiceChatIds().has("c1")).toBe(false);
    unsub();
    expect(seen.length).toBeGreaterThanOrEqual(2);
  });

  it("does not revive a stopped chat from a status patch", () => {
    setLiveVoiceChat("c2", false);
    patchLiveVoiceState("c2", { status: "speaking" });
    expect(getLiveVoiceChatIds().has("c2")).toBe(false);
  });
});
