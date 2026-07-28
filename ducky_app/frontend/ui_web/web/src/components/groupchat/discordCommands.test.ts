import { describe, expect, it } from "vitest";

import {
  allDiscordCommands,
  filterDiscordCommands,
  groupDiscordCommands,
} from "./discordCommands";

describe("discordCommands", () => {
  it("builds chat + agent catalog and filters by tokens", () => {
    const all = allDiscordCommands("!bob");
    expect(all.some((c) => c.name === "!bob whoami")).toBe(true);
    expect(all.some((c) => c.name === "discord_list_channels")).toBe(true);

    const filtered = filterDiscordCommands(all, "kick member");
    expect(filtered.map((c) => c.name)).toEqual(["discord_kick_member"]);

    const groups = groupDiscordCommands(filtered);
    expect(groups).toEqual([{ category: "Members", items: filtered }]);
  });
});
