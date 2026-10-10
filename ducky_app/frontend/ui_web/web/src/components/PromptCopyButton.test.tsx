// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";

const copyText = vi.hoisted(() => vi.fn(async () => true));
vi.mock("../utils/copyText", () => ({ copyText }));

import { AutomatedPromptMessage } from "./AutomatedPromptMessage";
import { EditableUserMessage } from "./EditableUserMessage";
import { parseAutomatedPrompt } from "../utils/automatedPrompt";

afterEach(() => { cleanup(); vi.clearAllMocks(); });

it("copies your own prompt in one click without opening the editor", async () => {
  const onResend = vi.fn();
  render(<EditableUserMessage text="Fix the sidebar" editable currentMode="agent" currentModel="m" onResend={onResend} />);
  await act(async () => { fireEvent.click(screen.getByLabelText("Copy prompt")); });
  expect(copyText).toHaveBeenCalledWith("Fix the sidebar");
  expect(screen.getByLabelText("Copied")).toBeTruthy();
  expect(screen.queryByPlaceholderText(/Edit and resend/)).toBeNull();
});

it("copies an agent's message without the protocol lines and without expanding the card", async () => {
  const prompt = parseAutomatedPrompt(
    "[ducky:agent-message] from Writer A (chat 6d24326d-07ef-4181-a65b-24eb22f1a655) [codex]\n"
    + "[ducky:agent-message] No reply is required.\n\nWord is maple.",
  )!;
  render(<AutomatedPromptMessage prompt={prompt} />);
  await act(async () => { fireEvent.click(screen.getByLabelText("Copy prompt")); });
  expect(copyText).toHaveBeenCalledWith("Word is maple.");
  expect(screen.getByRole("button", { name: /from Writer A/ }).getAttribute("aria-expanded")).toBe("false");
});
