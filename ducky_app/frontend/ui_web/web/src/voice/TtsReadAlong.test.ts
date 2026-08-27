import { describe, expect, it } from "vitest";

import { mapReadAlong } from "./TtsReadAlong";

const msg =
  "So this is a UEFN test island. Dogs hunt cats and cats flee. I can walk through the behaviors.";

describe("mapReadAlong", () => {
  it("matches a live sentence inside the bubble", () => {
    const hit = mapReadAlong(msg, "I can walk through the behaviors.", "I can walk through the behaviors.", 10);
    expect(hit).toEqual({
      spokenText: msg,
      charIndex: msg.indexOf("I can walk through the behaviors.") + 10,
    });
  });

  it("keeps speak-this-reply on the full source", () => {
    const hit = mapReadAlong(msg, msg, msg, 4);
    expect(hit).toEqual({ spokenText: msg, charIndex: 4 });
  });

  it("strips a group speaker prefix", () => {
    const hit = mapReadAlong(msg, "Ducky: Dogs hunt cats and cats flee.", "Ducky: Dogs hunt cats and cats flee.", 0);
    expect(hit?.spokenText).toBe(msg);
    expect(hit?.charIndex).toBe(msg.indexOf("Dogs hunt cats and cats flee."));
  });

  it("ignores process-talk that is not in the bubble", () => {
    expect(mapReadAlong(msg, "Running tool to ping.", "Running tool to ping.", 0)).toBeNull();
  });
});
