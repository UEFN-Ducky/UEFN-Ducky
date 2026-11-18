import { readFileSync } from "node:fs";
import { expect, it } from "vitest";
import * as tabs from "./panelTabs";
import * as panel from "./panel";

it("tab-id helpers behave as before", () => {
  expect(tabs.nextChatLayoutMode("full", false)).toBe("full");
  expect(tabs.nextChatLayoutMode("full", true)).toBe("sidebarHidden");
  expect(tabs.nextChatLayoutMode("sidebarHidden", true)).toBe("full");
  expect(tabs.nextChatLayoutMode("sidebarHidden", false)).toBe("full");
  expect(tabs.chatTabId("c1")).toBe("chat:c1");
  expect(tabs.fileTabId("Content\\Verse\\A.verse")).toBe("file:content/verse/a.verse");
  expect(tabs.terminalTabId("s1")).toBe("terminal:s1");
  expect(tabs.planTabId("c1")).toBe("plan:c1");
  expect(tabs.usageTabId(" OpenAI ")).toBe("usage:openai");
  expect(tabs.usageProviderIdFromTab("usage:openai")).toBe("openai");
  expect(tabs.usageProviderIdFromTab("chat:x")).toBe("");
  expect([tabs.settingsTabId(), tabs.changesTabId(), tabs.workflowsTabId()]).toEqual(["settings:main", "changes:main", "workflows:main"]);
  expect(tabs.isLegacyWorkflowsTabId("pipelines:main")).toBe(true);
  expect(tabs.isLegacyWorkflowsTabId("automations:x")).toBe(true);
  expect(tabs.isLegacyWorkflowsTabId("workflows:main")).toBe(false);
  expect(tabs.discordTabId()).toBe("discord:main");
  expect(tabs.discordTabId("main")).toBe("discord:main");
  expect(tabs.discordTabId("bot2")).toBe("discord:bot2");
  expect(tabs.discordBotIdFromTab("discord:main")).toBe("default");
  expect(tabs.discordBotIdFromTab("discord:bot2")).toBe("bot2");
  expect(tabs.discordBotIdFromTab("chat:x")).toBe("default");
  expect(tabs.duckyProfileTabId(" dev ")).toBe("ducky-profile:dev");
  expect(tabs.duckyProfileIdFromTab("ducky-profile:dev")).toBe("dev");
  expect(tabs.duckyProfileIdFromTab("chat:x")).toBe("");
});

it("panel.ts re-exports every helper and holds no function body", () => {
  for (const name of Object.keys(tabs)) {
    expect((panel as Record<string, unknown>)[name], name).toBe((tabs as Record<string, unknown>)[name]);
  }
  const src = readFileSync(new URL("./panel.ts", import.meta.url), "utf8");
  expect(src).not.toMatch(/^\s*export\s+(async\s+)?function\b/m);
  expect(src).not.toMatch(/^\s*(export\s+)?const\s+\w+\s*=/m);
});
