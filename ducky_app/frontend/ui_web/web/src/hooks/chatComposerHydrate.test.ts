import { describe, expect, it } from "vitest";
import { nextComposerFromChat } from "./chatComposerHydrate";

describe("nextComposerFromChat", () => {
  it("hydrates empty composer from chat model/agent after stub open", () => {
    expect(
      nextComposerFromChat({
        selectedModel: "",
        codingAgent: "ducky",
        thinkingEffort: "off",
        chatModel: "composer-2.5",
        chatCodingAgent: "cursor",
        chatThinkingEffort: "high",
      }),
    ).toEqual({
      selectedModel: "composer-2.5",
      codingAgent: "cursor",
      thinkingEffort: "high",
    });
  });

  it("does not overwrite a model the user already picked", () => {
    expect(
      nextComposerFromChat({
        selectedModel: "composer-2.5",
        codingAgent: "ducky",
        thinkingEffort: "off",
        chatModel: "composer-2.5",
        chatCodingAgent: "cursor",
      }),
    ).toBeNull();
  });

  it("stays null while the chat stub still has no model", () => {
    expect(
      nextComposerFromChat({
        selectedModel: "",
        codingAgent: "ducky",
        thinkingEffort: "off",
        chatModel: "",
        chatCodingAgent: "cursor",
      }),
    ).toBeNull();
  });
});
