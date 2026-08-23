import { describe, expect, it } from "vitest";

import { getLiveVoiceChatIds, setLiveVoiceChat, subscribeLiveVoiceChats } from "./liveChats";

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
});
