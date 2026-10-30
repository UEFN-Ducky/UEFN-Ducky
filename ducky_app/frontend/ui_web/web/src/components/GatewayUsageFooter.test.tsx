// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
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

  it("shows a refresh notice and button when the session is stale", () => {
    const onAction = () => {};
    render(
      <GatewayUsageFooter
        windows={[]}
        notice={{
          message: "Claude login expired. Log in to see 5-hour and weekly limits.",
          action: "login",
          action_label: "Log in",
          provider_id: "anthropic",
        }}
        onAction={onAction}
      />,
    );
    expect(screen.getByText(/Claude login expired/)).toBeTruthy();
    expect(screen.getByRole("button", { name: "Log in" })).toBeTruthy();
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

  it("toggles between accounts", () => {
    const picked: string[] = [];
    render(
      <GatewayUsageFooter
        windows={[{ id: "weekly", label: "Account 1 · Weekly limit", used: 30, limit: 100 }]}
        accountSwitch={{
          plugin_id: "anthropic",
          rpc: "set_claude_account",
          value: "1",
          options: [
            { id: "1", label: "Account 1" },
            { id: "2", label: "Account 2" },
          ],
        }}
        onSwitch={(id) => picked.push(id)}
      />,
    );
    const one = screen.getByRole("radio", { name: "Account 1" });
    const two = screen.getByRole("radio", { name: "Account 2" });
    expect(one.getAttribute("aria-checked")).toBe("true");
    expect((one as HTMLButtonElement).disabled).toBe(true);
    fireEvent.click(two);
    expect(picked).toEqual(["2"]);
  });

  it("shows the toggle even before any bars load", () => {
    render(
      <GatewayUsageFooter
        windows={[]}
        accountSwitch={{ plugin_id: "a", rpc: "r", value: "2", options: [{ id: "1", label: "A" }, { id: "2", label: "B" }] }}
      />,
    );
    expect(screen.getByRole("radio", { name: "B" }).getAttribute("aria-checked")).toBe("true");
  });
});

describe("effortGlow", () => {
  it("is 0 at Faster/off and 1 at Smarter", () => {
    expect(effortGlow(MENU, "off")).toBe(0);
    expect(effortGlow(MENU, "high")).toBe(1);
    expect(effortGlow(null, "high")).toBe(0);
  });
});
