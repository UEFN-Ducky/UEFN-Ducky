// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { PromptQueueBar } from "./PromptQueueBar";
import { makeQueuedPrompt } from "../hooks/promptQueue";

afterEach(cleanup);

describe("PromptQueueBar", () => {
  it("hands Edit to the composer immediately without opening an inline editor or sending", () => {
    const onEdit = vi.fn();
    const onSendNow = vi.fn();
    const onDelete = vi.fn();
    render(<PromptQueueBar
      items={[makeQueuedPrompt("revise me", { id: "q1", mode: "agent", model: "m" })!]}
      onEdit={onEdit} onSendNow={onSendNow} onDelete={onDelete}
    />);

    fireEvent.click(screen.getByRole("button", { name: "Edit queued prompt" }));
    expect(onEdit).toHaveBeenCalledExactlyOnceWith("q1");
    expect(onSendNow).not.toHaveBeenCalled();
    expect(onDelete).not.toHaveBeenCalled();
    expect(screen.queryByRole("textbox")).toBeNull();
    expect(screen.queryByRole("button", { name: "Save" })).toBeNull();
  });
});
