import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import {
  composerPlaceholder,
  filterCommands,
  matchSlashCommand,
  readSlashQuery,
  type SlashCommand,
} from "./slashCommands";

const noop = () => {};

const commands: SlashCommand[] = [
  { name: "agent", description: "", keywords: ["mode"], run: noop },
  { name: "ask", description: "", keywords: ["mode"], run: noop },
  { name: "goal", description: "", keywords: ["objective"], requiresArgument: true, run: noop },
  { name: "help", description: "", run: noop },
];

describe("readSlashQuery", () => {
  it("opens on a bare slash and reports an empty query", () => {
    expect(readSlashQuery("/")).toBe("");
  });

  it("reports the command name being typed", () => {
    expect(readSlashQuery("/go")).toBe("go");
  });

  it("stays closed when the slash is not the first character", () => {
    expect(readSlashQuery("fix Content/Verse/main.verse")).toBeNull();
    expect(readSlashQuery(" /goal")).toBeNull();
  });

  it("closes once the user moves on to the argument", () => {
    expect(readSlashQuery("/goal ")).toBeNull();
    expect(readSlashQuery("/goal ship the lobby")).toBeNull();
  });
});

describe("filterCommands", () => {
  it("returns everything for an empty query", () => {
    expect(filterCommands(commands, "")).toHaveLength(commands.length);
  });

  it("matches on a name prefix", () => {
    expect(filterCommands(commands, "a").map((c) => c.name)).toEqual(["agent", "ask"]);
  });

  it("matches on keywords but ranks name hits first", () => {
    expect(filterCommands(commands, "o").map((c) => c.name)).toEqual(["goal"]);
    expect(filterCommands(commands, "as").map((c) => c.name)).toEqual(["ask"]);
  });

  it("returns nothing for an unknown prefix", () => {
    expect(filterCommands(commands, "zz")).toEqual([]);
  });
});

describe("matchSlashCommand", () => {
  it("matches a bare command", () => {
    expect(matchSlashCommand("/help", commands)).toMatchObject({
      command: { name: "help" },
      argument: "",
    });
  });

  it("splits the argument off the command name", () => {
    expect(matchSlashCommand("/goal ship the lobby", commands)).toMatchObject({
      command: { name: "goal" },
      argument: "ship the lobby",
    });
  });

  it("is case insensitive on the name", () => {
    expect(matchSlashCommand("/HELP", commands)?.command.name).toBe("help");
  });

  it("ignores unknown commands so they still reach the ducky", () => {
    expect(matchSlashCommand("/nope", commands)).toBeNull();
  });

  it("ignores plain messages and paths", () => {
    expect(matchSlashCommand("hello", commands)).toBeNull();
    expect(matchSlashCommand("look at /goal in the docs", commands)).toBeNull();
  });
});

describe("composerPlaceholder", () => {
  const idle = {
    liveVoiceMuted: false,
    isGroup: false,
    groupEmpty: false,
    agentRunning: false,
    noModelsAvailable: false,
    modelLabel: "Ducky",
  };

  it("tells the idle ask composer that / opens commands", () => {
    expect(composerPlaceholder(idle)).toContain("/ for commands");
    expect(composerPlaceholder(idle)).toContain("Ask Ducky");
  });

  it("does not tack the hint onto muted, no-models, or follow-up copy", () => {
    expect(composerPlaceholder({ ...idle, liveVoiceMuted: true })).not.toContain("/ for commands");
    expect(composerPlaceholder({ ...idle, noModelsAvailable: true })).not.toContain("/ for commands");
    expect(composerPlaceholder({ ...idle, agentRunning: true })).not.toContain("/ for commands");
  });
});

describe("slash menu host", () => {
  it("renders the palette outside the container-query input box", () => {
    const src = readFileSync(join(dirname(fileURLToPath(import.meta.url)), "ChatPane.tsx"), "utf8");
    const menu = src.indexOf("<SlashCommandMenu");
    const box = src.indexOf("chat-pane-input-box");
    expect(menu).toBeGreaterThan(-1);
    expect(box).toBeGreaterThan(-1);
    expect(menu).toBeLessThan(box);
  });
});
