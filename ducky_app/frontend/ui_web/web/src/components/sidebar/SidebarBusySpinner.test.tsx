// @vitest-environment jsdom
import { act, cleanup, render } from "@testing-library/react";
import { afterEach, expect, it } from "vitest";
import { SidebarBusySpinner, leadSpinner } from "./SidebarBusySpinner";

afterEach(() => cleanup());

function Rows({ busy }: { busy: number }) {
  return (
    <ul>
      {Array.from({ length: busy }, (_, i) => (
        <li key={i} data-row={i}>
          <SidebarBusySpinner title="Agent working" />
        </li>
      ))}
    </ul>
  );
}

const turning = () => document.querySelectorAll(".sidebar-agent-spinner--shared.is-lead");
const rings = () => document.querySelectorAll(".sidebar-agent-spinner");

it("eleven busy rows run one spinner animation, the rest stand still", () => {
  render(<Rows busy={11} />);
  expect(rings().length).toBe(11);
  expect(turning().length).toBe(1);
  expect((turning()[0].closest("li") as HTMLElement).dataset.row).toBe("0");
});

it("when the turning one goes, the next takes over; with none busy nothing turns", () => {
  const { rerender } = render(<Rows busy={3} />);
  act(() => rerender(<Rows busy={2} />)); // row 2 unmounts: row 0 still turns
  expect(turning().length).toBe(1);
  const view = render(<SidebarBusySpinner title="later" />);
  expect(turning().length).toBe(1);
  act(() => rerender(<Rows busy={0} />));
  expect(turning().length).toBe(1); // the later one took over
  expect(turning()[0].getAttribute("title")).toBe("later");
  view.unmount();
  expect(turning().length).toBe(0);
  expect(leadSpinner()).toBe("");
});

it("only the turning ring has the animation in the stylesheet", async () => {
  const { readFileSync } = await import("node:fs");
  const css = readFileSync(`${process.cwd()}/src/theme/styles/components-inline.css`, "utf8");
  expect(css).toMatch(/\.sidebar-agent-spinner--shared\s*\{\s*animation:\s*none;/);
  expect(css).toMatch(/\.sidebar-agent-spinner--shared\.is-lead\s*\{\s*animation:\s*spin-slow/);
});
