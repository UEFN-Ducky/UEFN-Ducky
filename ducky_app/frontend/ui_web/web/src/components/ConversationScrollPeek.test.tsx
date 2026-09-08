// @vitest-environment jsdom
import { act, cleanup, fireEvent, render } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import type { ChatTurn } from "../utils/chatMessageGroups";
import { ConversationScrollPeek } from "./ConversationScrollPeek";

function stubScrollTo(el: HTMLElement, onJump?: (top: number) => void) {
  el.scrollTo = ((a?: ScrollToOptions | number, b?: number) => {
    const top = typeof a === "number" ? (b ?? 0) : Number(a?.top ?? 0);
    el.scrollTop = top;
    onJump?.(top);
  }) as typeof el.scrollTo;
}

function stubPeekRects() {
  const box = (top: number, height: number, width: number): DOMRect => ({
    x: 0,
    y: top,
    top,
    left: 0,
    bottom: top + height,
    right: width,
    width,
    height,
    toJSON() {
      return {};
    },
  });
  HTMLElement.prototype.getBoundingClientRect = function getBoundingClientRect() {
    if (this.hasAttribute("data-chat-scroll-peek-track")) return box(0, 400, 20);
    return box(0, 800, 400);
  };
  // jsdom performs no layout, so clientHeight is 0 for everything. The peek sizes
  // its tick stack from the rail's own height, and a zero-height rail has no ticks
  // to hover — without this the component renders but can never open.
  Object.defineProperty(HTMLElement.prototype, "clientHeight", {
    configurable: true,
    get(this: HTMLElement) {
      return this.hasAttribute("data-chat-scroll-peek") ? 400 : 0;
    },
  });
}

function turns(n: number): ChatTurn[] {
  return Array.from({ length: n }, (_, i) => ({
    id: `turn-${i}`,
    query: { kind: "bubble", id: `q${i}`, role: "user", text: `ask ${i} about the build` },
    responses: [{ kind: "bubble", id: `a${i}`, role: "assistant", text: `answer ${i} is ready` }],
  }));
}

function mountPeek(scroller: HTMLElement) {
  return render(
    <ConversationScrollPeek
      turns={turns(20)}
      chunkHeights={Array(3).fill(1000)}
      turnsPerChunk={8}
      scroller={scroller}
      heightsTick={1}
    />,
  );
}

const origGetBoundingClientRect = HTMLElement.prototype.getBoundingClientRect;
const origClientHeight = Object.getOwnPropertyDescriptor(HTMLElement.prototype, "clientHeight");

describe("ConversationScrollPeek", () => {
  afterEach(() => {
    HTMLElement.prototype.getBoundingClientRect = origGetBoundingClientRect;
    if (origClientHeight) Object.defineProperty(HTMLElement.prototype, "clientHeight", origClientHeight);
    cleanup();
  });

  it("does not open the card while scrolling", () => {
    const scroller = document.createElement("div");
    Object.defineProperties(scroller, {
      scrollHeight: { value: 4000, configurable: true },
      clientHeight: { value: 800, configurable: true },
      scrollTop: { value: 2000, writable: true, configurable: true },
    });
    stubScrollTo(scroller);
    mountPeek(scroller);

    act(() => {
      scroller.dispatchEvent(new Event("scroll"));
    });

    expect(document.querySelector("[data-chat-scroll-peek-card]")).toBeNull();
  });

  it("shows the hovered turn and hides when the pointer leaves", () => {
    const scroller = document.createElement("div");
    Object.defineProperties(scroller, {
      scrollHeight: { value: 4000, configurable: true },
      clientHeight: { value: 800, configurable: true },
      scrollTop: { value: 0, writable: true, configurable: true },
    });
    stubScrollTo(scroller);
    stubPeekRects();
    mountPeek(scroller);

    const track = document.querySelector("[data-chat-scroll-peek-track]")!;
    fireEvent.pointerEnter(track, { clientY: 200 });

    const card = document.querySelector("[data-chat-scroll-peek-card]");
    expect(card).toBeTruthy();
    expect(card?.textContent).toMatch(/ask \d+/);
    expect(card?.textContent).toMatch(/answer \d+/);

    fireEvent.pointerMove(track, { clientY: 360 });
    const moved = document.querySelector("[data-chat-scroll-peek-card]");
    expect(moved).toBeTruthy();
    expect(moved?.getAttribute("style") ?? "").toMatch(/--peek-y/);

    fireEvent.pointerLeave(track);
    expect(document.querySelector("[data-chat-scroll-peek-card]")).toBeNull();
  });

  it("jumps the scroller when the timeline is clicked", () => {
    const scroller = document.createElement("div");
    Object.defineProperties(scroller, {
      scrollHeight: { value: 4000, configurable: true },
      clientHeight: { value: 800, configurable: true },
      scrollTop: { value: 0, writable: true, configurable: true },
    });
    let jumped = 0;
    stubScrollTo(scroller, (top) => {
      jumped = top;
    });
    stubPeekRects();

    render(
      <ConversationScrollPeek
        turns={turns(10)}
        chunkHeights={[2000]}
        turnsPerChunk={8}
        scroller={scroller}
        heightsTick={1}
      />,
    );

    const track = document.querySelector("[data-chat-scroll-peek-track]")!;
    fireEvent.click(track, { clientY: 350 });
    expect(jumped).toBeGreaterThan(0);
  });
});
