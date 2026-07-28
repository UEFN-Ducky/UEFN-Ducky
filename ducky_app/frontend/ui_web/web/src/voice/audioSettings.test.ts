import { describe, expect, it, vi } from "vitest";

import { applyOutputDevice, effectivePlaybackVolume } from "./audioSettings";
import { micAccessAllowed, micConstraints } from "./micPermission";

describe("audio settings helpers", () => {
  it("micAccessAllowed only when allow", () => {
    expect(micAccessAllowed("allow")).toBe(true);
    expect(micAccessAllowed("ask")).toBe(false);
    expect(micAccessAllowed("block")).toBe(false);
  });

  it("effectivePlaybackVolume zeroes when muted", () => {
    expect(effectivePlaybackVolume(0.8, true)).toBe(0);
    expect(effectivePlaybackVolume(0.8, false)).toBe(0.8);
    expect(effectivePlaybackVolume(1.5, false)).toBe(1);
    expect(effectivePlaybackVolume(-0.2, false)).toBe(0);
  });

  it("micConstraints pins exact deviceId when set", () => {
    const c = micConstraints("abc-mic");
    const audio = c.audio as MediaTrackConstraints;
    expect(audio.deviceId).toEqual({ exact: "abc-mic" });
  });

  it("micConstraints omits deviceId for Windows default", () => {
    const c = micConstraints("");
    const audio = c.audio as MediaTrackConstraints;
    expect(audio.deviceId).toBeUndefined();
  });

  it("applyOutputDevice calls setSinkId with saved id", async () => {
    const setSinkId = vi.fn(async () => undefined);
    // Patch cache via save would need API; call with empty cache (default "").
    await applyOutputDevice({ setSinkId });
    expect(setSinkId).toHaveBeenCalledWith("");
  });
});
