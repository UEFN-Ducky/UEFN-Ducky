// @vitest-environment jsdom
import { cleanup, render } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { DuckyProfilePicker } from "./DuckyProfilePicker";
import type { AgentProfileDto } from "../../types/panel";

afterEach(cleanup);

const verse: AgentProfileDto = {
  id: "verse",
  name: "Verse Coder",
  ducky_style: "hacker",
  ducky_personality: "",
  disabled_packs: [],
  disabled_tool_ids: [],
  kind: "custom",
};

describe("DuckyProfilePicker", () => {
  it("disables tiles while creating so Create new cannot inherit Saving…", () => {
    const onBlank = vi.fn();
    const onPick = vi.fn();
    const { getByLabelText } = render(
      <DuckyProfilePicker
        profiles={[verse]}
        creating
        onBlank={onBlank}
        onPick={onPick}
        onEditProfile={() => {}}
      />,
    );
    expect((getByLabelText("Create new — custom setup") as HTMLButtonElement).disabled).toBe(true);
    expect((getByLabelText("Verse Coder") as HTMLButtonElement).disabled).toBe(true);
    expect(document.body.textContent).toContain("Creating…");
  });
});
