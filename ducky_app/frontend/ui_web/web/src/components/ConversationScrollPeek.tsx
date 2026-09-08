import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";

import type { ChatTurn } from "../utils/chatMessageGroups";
import {
  indexAtFraction,
  peekLinesForTurn,
  peekTickLayout,
  turnOffsetsFromChunkHeights,
} from "../utils/chatScrollPeek";

const MIN_TURNS = 3;
const TICK_GAP = 7;

interface ConversationScrollPeekProps {
  turns: ChatTurn[];
  chunkHeights: readonly number[];
  turnsPerChunk: number;
  scroller: HTMLElement | null;
  /** Bump when measured heights change so offsets recompute. */
  heightsTick: number;
  /** Parent should release tail-follow before scrolling so the jump sticks. */
  onJump?: (top: number) => void;
}

/**
 * Codex-style conversation peek: packed left-edge ticks + a floating snippet.
 * Card opens only while the pointer is on the tick strip — never on scroll.
 */
export function ConversationScrollPeek({
  turns,
  chunkHeights,
  turnsPerChunk,
  scroller,
  heightsTick,
  onJump,
}: ConversationScrollPeekProps) {
  const rootRef = useRef<HTMLDivElement>(null);
  const floatRef = useRef<HTMLDivElement>(null);
  const lastIndex = useRef(-1);
  const [open, setOpen] = useState(false);
  const [index, setIndex] = useState(0);
  const [anchorY, setAnchorY] = useState(0);
  const [overflow, setOverflow] = useState(false);
  const [trackH, setTrackH] = useState(480);

  const offsets = useMemo(
    () => turnOffsetsFromChunkHeights(chunkHeights, turnsPerChunk, turns.length),
    // heightsTick is the signal that chunkHeights contents changed (same array ref).
    [chunkHeights, turnsPerChunk, turns.length, heightsTick],
  );

  const layout = useMemo(
    () => peekTickLayout(turns.length, trackH, TICK_GAP),
    [turns.length, trackH],
  );

  const place = useCallback((y: number) => {
    const h = rootRef.current?.clientHeight ?? 0;
    const clamped = h > 0 ? Math.min(h - 48, Math.max(48, y)) : y;
    floatRef.current?.style.setProperty("--peek-y", `${clamped}px`);
    return clamped;
  }, []);

  const show = useCallback(
    (i: number, y: number) => {
      lastIndex.current = i;
      setIndex(i);
      setAnchorY(place(y));
      setOpen(true);
    },
    [place],
  );

  const indexFromClientY = useCallback(
    (clientY: number): { i: number; y: number } | null => {
      const track = rootRef.current?.querySelector<HTMLElement>("[data-chat-scroll-peek-track]");
      const root = rootRef.current;
      if (!track || !root || layout.indexes.length === 0) return null;
      const trackRect = track.getBoundingClientRect();
      const frac = (clientY - trackRect.top) / Math.max(1, trackRect.height);
      const slot = indexAtFraction(frac, layout.indexes.length);
      const i = layout.indexes[slot];
      if (i == null || i < 0) return null;
      return { i, y: layout.start + slot * layout.gap };
    },
    [layout],
  );

  const onScrub = useCallback(
    (clientY: number) => {
      const hit = indexFromClientY(clientY);
      if (!hit) return;
      const y = place(hit.y);
      if (hit.i !== lastIndex.current) {
        lastIndex.current = hit.i;
        setIndex(hit.i);
        setAnchorY(y);
      }
      setOpen(true);
    },
    [indexFromClientY, place],
  );

  useLayoutEffect(() => {
    const root = rootRef.current;
    if (!root) return;
    const read = () => setTrackH(root.clientHeight);
    read();
    let ro: ResizeObserver | undefined;
    if (typeof ResizeObserver !== "undefined") {
      ro = new ResizeObserver(read);
      ro.observe(root);
    }
    return () => ro?.disconnect();
  }, [turns.length]);

  useEffect(() => {
    if (!scroller) return;
    const measure = () => {
      setOverflow(scroller.scrollHeight > scroller.clientHeight + 32);
    };
    measure();
    let ro: ResizeObserver | undefined;
    if (typeof ResizeObserver !== "undefined") {
      ro = new ResizeObserver(measure);
      ro.observe(scroller);
    }
    return () => {
      ro?.disconnect();
    };
  }, [scroller]);

  const jumpTo = useCallback(
    (i: number) => {
      if (i < 0 || i >= offsets.length) return;
      const top = offsets[i];
      if (onJump) onJump(top);
      else if (scroller) scroller.scrollTo({ top, behavior: "auto" });
    },
    [scroller, offsets, onJump],
  );

  if (turns.length < MIN_TURNS) return null;

  const turn = turns[index];
  const lines = turn ? peekLinesForTurn(turn) : { query: "", reply: "", more: "" };
  const showCard = open && overflow && !!turn;

  return (
    <div
      ref={rootRef}
      className={`chat-scroll-peek${overflow ? " is-ready" : ""}${showCard ? " is-open" : ""}`}
      data-chat-scroll-peek
      aria-hidden={!showCard}
    >
      <div
        className="chat-scroll-peek-track"
        data-chat-scroll-peek-track
        style={{ top: layout.start, height: Math.max(layout.stackH, 1) }}
        onPointerEnter={(e) => onScrub(e.clientY)}
        onPointerMove={(e) => onScrub(e.clientY)}
        onPointerLeave={() => {
          lastIndex.current = -1;
          setOpen(false);
        }}
        onClick={(e) => {
          const hit = indexFromClientY(e.clientY);
          if (!hit) return;
          show(hit.i, hit.y);
          jumpTo(hit.i);
        }}
      >
        {layout.indexes.map((i, s) => (
          <span
            key={turns[i]?.id ?? i}
            className={`chat-scroll-peek-tick${i === index && open ? " is-active" : ""}`}
            style={{ top: s * layout.gap }}
          />
        ))}
      </div>
      {showCard ? (
        <div
          ref={floatRef}
          className="chat-scroll-peek-float"
          style={{ ["--peek-y" as string]: `${anchorY}px` }}
          data-chat-scroll-peek-card
        >
          <span className="chat-scroll-peek-pip" />
          <div className="chat-scroll-peek-card">
            {lines.query ? <div className="chat-scroll-peek-query">{lines.query}</div> : null}
            {lines.reply ? <div className="chat-scroll-peek-reply">{lines.reply}</div> : null}
            {lines.more ? <div className="chat-scroll-peek-more">{lines.more}</div> : null}
          </div>
        </div>
      ) : null}
    </div>
  );
}
