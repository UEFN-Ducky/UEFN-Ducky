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

  it("draws a disabled slider per window", () => {
    render(
      <GatewayUsageFooter
        windows={[
          { id: "hourly", label: "Hourly", used: 12, limit: 40, unit: "requests" },
          { id: "monthly", label: "Monthly", used: 1, limit: 2 },
        ]}
      />,
    );
    expect(screen.getByLabelText("Hourly 12 / 40 requests")).toHaveProperty("disabled", true);
    expect(screen.getByLabelText("Monthly 1 / 2")).toBeTruthy();
  });
});

describe("effortGlow", () => {
  it("is 0 at Faster/off and 1 at Smarter", () => {
    expect(effortGlow(MENU, "off")).toBe(0);
    expect(effortGlow(MENU, "high")).toBe(1);
    expect(effortGlow(null, "high")).toBe(0);
  });
});
