// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { GroupEditor, RunAtOnceSlider } from "./GroupEditor";
import type { SwarmEdit } from "./SeatEditor";

afterEach(cleanup);

const steps = [{ id: "p0", content: "P0 Engine fork" }];

function renderGroup(muted: boolean) {
  const edits: SwarmEdit[] = [];
  render(<GroupEditor group={{ group: "Engine: UE 5.8 web fork", muted }} steps={steps} onEdit={(e) => edits.push(e)} />);
  return edits;
}

describe("GroupEditor", () => {
  it("toggling mute puts {mute: group} in edits[], and back", () => {
    const edits = renderGroup(false);
    fireEvent.click(screen.getByLabelText("Muted"));
    cleanup();
    const back = renderGroup(true);
    fireEvent.click(screen.getByLabelText("Muted"));
    expect(edits).toEqual([{ mute: "Engine: UE 5.8 web fork" }]);
    expect(back).toEqual([{ unmute: "Engine: UE 5.8 web fork" }]);
  });

  it("renames and starts after a step", () => {
    const edits = renderGroup(false);
    const name = screen.getByLabelText("Group name");
    fireEvent.change(name, { target: { value: "Engine" } });
    fireEvent.blur(name);
    fireEvent.change(screen.getByLabelText("Start after step"), { target: { value: "p0" } });
    expect(edits).toEqual([
      { rename: "Engine: UE 5.8 web fork", to: "Engine" },
      { group: "Engine: UE 5.8 web fork", start_after: "p0" },
    ]);
  });

  it("team switches emit {group, key}", () => {
    const edits = renderGroup(false);
    fireEvent.click(screen.getByLabelText("Trust plugin drafts"));
    expect(edits).toEqual([{ group: "Engine: UE 5.8 web fork", trust_drafts: true }]);
  });

  it("run-at-once slider goes up to every seat, past the PC hint, and emits on release", () => {
    const edits: SwarmEdit[] = [];
    render(<RunAtOnceSlider value={4} seats={12} pcLimit={6} onEdit={(e) => edits.push(e)} />);
    const slider = screen.getByLabelText("Run at once") as HTMLInputElement;
    expect(slider.max).toBe("12");
    expect(screen.getByText("4 of 12")).toBeTruthy();
    fireEvent.change(slider, { target: { value: "12" } });
    fireEvent.pointerUp(slider);
    expect(edits).toEqual([{ run_at_once: 12 }]);
  });
});
