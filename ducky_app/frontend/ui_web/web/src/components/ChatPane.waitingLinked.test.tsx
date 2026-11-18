// @vitest-environment jsdom
import { act, cleanup, render } from "@testing-library/react";
import { memo, useState } from "react";
import { afterEach, expect, it } from "vitest";

import type { LinkedAgent } from "../types/panel";
import { useWaitingLinked } from "./ChatPane";

afterEach(cleanup);

let listRenders = 0;
const List = memo(function List({ waiting }: { waiting: LinkedAgent[] }) {
  listRenders += 1;
  return <div>{waiting.length}</div>;
});

let pushFrame: (() => void) | null = null;
let setAgents: ((a: LinkedAgent[]) => void) | null = null;

function Pane({ initial, isGroup = false }: { initial: LinkedAgent[]; isGroup?: boolean }) {
  const [, setFrame] = useState(0);
  const [agents, set] = useState(initial);
  pushFrame = () => setFrame((n) => n + 1);
  setAgents = set;
  // Each render gets a fresh copy, like useLinkedAgents after a merge.
  const waiting = useWaitingLinked(agents.map((a) => ({ ...a })), isGroup);
  return <List waiting={waiting} />;
}

const running: LinkedAgent = { childConvId: "c1", title: "Helper", status: "running" };

it("20 stream frames render the memoized list once", () => {
  listRenders = 0;
  render(<Pane initial={[running, { childConvId: "c2", title: "Done", status: "done" }]} />);
  for (let i = 0; i < 20; i += 1) act(() => pushFrame!());
  expect(listRenders).toBe(1);
});

it("a real change still reaches the list", () => {
  listRenders = 0;
  const { container } = render(<Pane initial={[running]} />);
  act(() => setAgents!([{ ...running, status: "done" }]));
  expect(listRenders).toBe(2);
  expect(container.textContent).toBe("0");
});

it("groups never wait", () => {
  const { container } = render(<Pane initial={[running]} isGroup />);
  expect(container.textContent).toBe("0");
});
