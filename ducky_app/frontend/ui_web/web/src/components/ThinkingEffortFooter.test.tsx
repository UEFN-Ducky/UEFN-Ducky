// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { effortFire, effortSliderColor, ThinkingEffortFooter } from "./ThinkingEffortFooter";

const MENU = {
  lo: "Faster",
  hi: "Smarter",
  levels: [
    { id: "off", label: "Off", thinking_tokens: 0, hint: "No extended thinking" },
    { id: "low", label: "Low", thinking_tokens: null },
    { id: "high", label: "High", thinking_tokens: null },
  ],
};

describe("ThinkingEffortFooter", () => {
  afterEach(cleanup);

  it("keeps the slider on screen and disabled when the gateway sent no menu", () => {
    render(<ThinkingEffortFooter menu={null} modelName="GPT-6-Astra" effort="high" onChange={() => {}} />);
    const slider = screen.getByRole("slider", { name: "Thinking effort" });
    expect(slider).toBeTruthy();
    expect(slider).toHaveProperty("disabled", true);
    expect(screen.getByText("GPT-6-Astra · Off")).toBeTruthy();
    expect(screen.queryByText("This model has no extended thinking")).toBeNull();
  });

  it("moves through gateway levels when the menu is present", () => {
    const onChange = vi.fn();
    render(
      <ThinkingEffortFooter menu={MENU} modelName="GPT-6-Astra" effort="off" onChange={onChange} />,
    );
    const slider = screen.getByRole("slider", { name: "Thinking effort" }) as HTMLInputElement;
    expect(slider.disabled).toBe(false);
    fireEvent.change(slider, { target: { value: "2" } });
    expect(onChange).toHaveBeenCalledWith("high");
  });
});

describe("effortFire", () => {
  it("stays off at Faster and erupts near Smarter", () => {
    expect(effortFire(0)).toEqual({ scale: 0, opacity: 0 });
    expect(effortFire(0.5).scale).toBeCloseTo(0.5 ** 3 * 0.55);
    expect(effortFire(1).scale).toBeCloseTo(0.55);
    expect(effortFire(1).opacity).toBe(1);
  });
});

describe("effortSliderColor", () => {
  it("mixes Ducky green/amber/red tokens", () => {
    expect(effortSliderColor(0)).toContain("var(--green)");
    expect(effortSliderColor(0.5)).toContain("var(--amber)");
    expect(effortSliderColor(1)).toContain("var(--red)");
  });
});
