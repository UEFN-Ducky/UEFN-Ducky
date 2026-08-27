import { describe, expect, it } from "vitest";

import { pullSentences, stripForSpeech, SentenceQueue } from "./sentenceQueue";

describe("stripForSpeech", () => {
  it("removes fenced and inline code", () => {
    expect(stripForSpeech("Hi ```code block``` there `x` end")).toBe("Hi there end");
  });

  it("keeps link labels", () => {
    expect(stripForSpeech("See [docs](https://example.com) now")).toBe("See docs now");
  });

  it("drops markdown table rows", () => {
    expect(stripForSpeech("Intro.\n| A | B |\n| x | y |\nOut.")).toMatch(/Intro\.\s+Out\./);
  });
});

describe("pullSentences", () => {
  it("emits complete sentences and keeps remainder", () => {
    const { sentences, remainder } = pullSentences("Hello there. More");
    expect(sentences).toEqual(["Hello there."]);
    expect(remainder).toBe("More");
  });

  it("flushes remainder when forced", () => {
    const { sentences, remainder } = pullSentences("Almost done", true);
    expect(sentences).toEqual(["Almost done"]);
    expect(remainder).toBe("");
  });
});

describe("SentenceQueue", () => {
  it("enqueues deltas into speakable sentences", () => {
    const q = new SentenceQueue();
    expect(q.enqueue("Hello ")).toEqual([]);
    expect(q.enqueue("world. Next")).toEqual(["Hello world."]);
    expect(q.flush()).toEqual(["Next"]);
  });
});
