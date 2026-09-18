// @vitest-environment jsdom
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { effortGlow, GatewayUsageFooter } from "./GatewayUsageFooter";

const MENU = {
  lo: "Faster",
  hi: "Smarter",
  levels: [
    { id: "off", label: "Off", thinking_tokens: 0 },
    { id: "low", label: "Low" },
    { id: "high", label: "High" },
  ],
};

describe("GatewayUsageFooter", () => {
  afterEach(cleanup);

  it("renders nothing when the gateway sent no windows", () => {
    const { container } = render(<GatewayUsageFooter windows={[]} />);
    expect(container.innerHTML).toBe("");
  });

  it("draws plan bars with reset and percent", () => {
    render(
      <GatewayUsageFooter
        windows={[
          { id: "hourly", label: "5-hour limit", used: 100, limit: 100, reset: "Resets in 2 hr 41 min", readout: "100%" },
          { id: "weekly", label: "Weekly limit", used: 99, limit: 100, reset: "Resets in 4d 3h", readout: "1% left" },
        ]}
      />,
    );
    expect(screen.getByText("5-hour limit")).toBeTruthy();
    expect(screen.getByText("100%")).toBeTruthy();
    expect(screen.getByText("1% left")).toBeTruthy();
    expect(screen.getByText("Resets in 2 hr 41 min")).toBeTruthy();
  });
});

describe("effortGlow", () => {
  it("is 0 at Faster/off and 1 at Smarter", () => {
    expect(effortGlow(MENU, "off")).toBe(0);
    expect(effortGlow(MENU, "high")).toBe(1);
    expect(effortGlow(null, "high")).toBe(0);
  });
});
