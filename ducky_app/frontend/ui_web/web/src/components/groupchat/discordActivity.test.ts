import { describe, expect, it, beforeEach } from "vitest";
import {
  bumpDiscordActivity,
  _getDiscordActivityMapSnapshotForTests,
  _resetDiscordActivityForTests,
} from "./discordActivity";

describe("discordActivity map snapshot", () => {
  beforeEach(() => {
    _resetDiscordActivityForTests();
  });

  it("returns the same Map reference until activity changes (useSyncExternalStore contract)", () => {
    const a = _getDiscordActivityMapSnapshotForTests();
    const b = _getDiscordActivityMapSnapshotForTests();
    expect(a).toBe(b);

    bumpDiscordActivity("bot-1", 1);
    const c = _getDiscordActivityMapSnapshotForTests();
    expect(c).not.toBe(a);
    expect(c.get("bot-1")).toBe(1);

    const d = _getDiscordActivityMapSnapshotForTests();
    expect(d).toBe(c);
  });
});
