import { describe, expect, it } from "vitest";
import {
  DEFAULT_SOUNDS,
  normalizeSounds,
  parseSoundRef,
  resolveSoundRef,
  soundUrlForRef,
} from "./soundFx";

describe("soundFx mapping", () => {
  it("normalizes missing/partial settings onto defaults", () => {
    const n = normalizeSounds(null);
    expect(n.enabled).toBe(false);
    expect(n.mapping["agent.done"]).toBe("builtin:ding");
  });

  it("resolves hook → soundRef with empty fallback", () => {
    const settings = normalizeSounds({
      enabled: true,
      volume: 0.8,
      mapping: { "tab.changed": "builtin:click", "agent.done": "" },
    });
    expect(resolveSoundRef(settings, "tab.changed")).toBe("builtin:click");
    expect(resolveSoundRef(settings, "agent.done")).toBe("");
    expect(resolveSoundRef(settings, "unknown.hook")).toBe(DEFAULT_SOUNDS.mapping["unknown.hook"] ?? "");
  });

  it("parses soundRef kinds", () => {
    expect(parseSoundRef("")).toEqual({ kind: "none" });
    expect(parseSoundRef("builtin:chime")).toEqual({ kind: "builtin", name: "chime" });
    expect(parseSoundRef("builtin:nope")).toEqual({ kind: "none" });
    expect(parseSoundRef("plugin:discord:ping")).toEqual({
      kind: "plugin",
      pluginId: "discord",
      soundId: "ping",
    });
    expect(parseSoundRef("file:my_sound_ab12.wav")).toEqual({
      kind: "file",
      filename: "my_sound_ab12.wav",
    });
    expect(parseSoundRef("file:../escape.wav")).toEqual({
      kind: "file",
      filename: "..escape.wav",
    });
  });

  it("builds URLs for plugin and file refs", () => {
    expect(
      soundUrlForRef("plugin:demo:ping", { "demo:ping": "assets/ping.wav" }),
    ).toBe("/plugin-ui/demo/assets/ping.wav");
    expect(soundUrlForRef("plugin:demo:missing", {})).toBeNull();
    expect(soundUrlForRef("file:hello.mp3")).toBe("/user-sounds/hello.mp3");
    expect(soundUrlForRef("builtin:click")).toBeNull();
  });
});
