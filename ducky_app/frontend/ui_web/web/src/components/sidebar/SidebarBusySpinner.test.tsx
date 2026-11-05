// @vitest-environment jsdom
import { cleanup, render } from "@testing-library/react";
import { afterEach, expect, it } from "vitest";
import { SidebarBusySpinner } from "./SidebarBusySpinner";

afterEach(() => cleanup());

it("every busy row's ring turns, not only the first one mounted", async () => {
  // Nov 5 2026: only the first ring turned; it sat off screen and every visible agent looked stuck.
  render(
    <ul>
      {Array.from({ length: 4 }, (_, i) => (
        <li key={i}>
          <SidebarBusySpinner title="Agent working" />
        </li>
      ))}
    </ul>,
  );
  const rings = document.querySelectorAll(".sidebar-agent-spinner");
  expect(rings.length).toBe(4);
  expect(document.querySelectorAll(".sidebar-agent-spinner--shared").length).toBe(0);
  const { readFileSync } = await import("node:fs");
  const css = readFileSync(`${process.cwd()}/src/theme/styles/components-inline.css`, "utf8");
  expect(css).not.toMatch(/\.sidebar-agent-spinner--shared\s*\{\s*animation:\s*none/);
});
