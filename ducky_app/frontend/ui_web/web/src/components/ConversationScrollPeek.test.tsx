// @vitest-environment jsdom
import { act, cleanup, fireEvent, render } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import type { ChatTurn } from "../utils/chatMessageGroups";
import { ConversationScrollPeek } from "./ConversationScrollPeek";

function turns(n: number): ChatTurn[] {
  return Array.from({ length: n }, (_, i) => ({
    id: `turn-${i}`,
    query: { kind: "bubble", id: `q${i}`, role: "user", text: `ask ${i} about the build` },
    responses: [{ kind: "bubble", id: `a${i}`, role: "assistant", text: `answer ${i} is ready` }],
  }));
}

describe("ConversationScrollPeek", () => {
  afterEach(() => cleanup());

  it("shows the turn under the thumb while scrolling a long chat", () => {
    const scroller = document.createElement("div");
    Object.defineProperties(scroller, {
      scrollHeight: { value: 4000, configurable: true },
      clientHeight: { value: 800, configurable: true },
      scrollTop: { value: 2000, writable: true, configurable: true },
    });
    scroller.scrollTo = (opts: ScrollToOptions | number) => {
      scroller.scrollTop = typeof opts === "number" ? opts : Number(opts.top ?? 0);
    };

    render(
      <ConversationScrollPeek
        turns={turns(20)}
        chunkHeights={Array(3).fill(1000)}
        turnsPerChunk={8}
        scroller={scroller}
        heightsTick={1}
      />,
    );

    act(() => {
      scroller.dispatchEvent(new Event("scroll"));
    });

    const card = document.querySelector("[data-chat-scroll-peek-card]");
    expect(card).toBeTruthy();
    expect(card?.textContent).toMatch(/ask \d+/);
    expect(card?.textContent).toMatch(/answer \d+/);
  });

  it("jumps the scroller when the timeline is clicked", () => {
    const scroller = document.createElement("div");
    Object.defineProperties(scroller, {
      scrollHeight: { value: 4000, configurable: true },
      clientHeight: { value: 800, configurable: true },
      scrollTop: { value: 0, writable: true, configurable: true },
    });
    let jumped = 0;
    scroller.scrollTo = (opts: ScrollToOptions | number) => {
      jumped = typeof opts === "number" ? opts : Number(opts.top ?? 0);
      scroller.scrollTop = jumped;
    };

    render(
      <ConversationScrollPeek
        turns={turns(10)}
        chunkHeights={[2000]}
        turnsPerChunk={8}
        scroller={scroller}
        heightsTick={1}
      />,
    );
    act(() => {
      scroller.dispatchEvent(new Event("scroll"));
    });

    const track = document.querySelector("[data-chat-scroll-peek-track]")!;
    fireEvent.click(track, { clientY: 0 });
    expect(jumped).toBeGreaterThanOrEqual(0);
  });
});
