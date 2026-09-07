import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import type { ChatTurn } from "../utils/chatMessageGroups";
import {
  indexAtFraction,
  peekLinesForTurn,
  turnOffsetsFromChunkHeights,
  visibleTickIndexes,
} from "../utils/chatScrollPeek";

const HIDE_AFTER_MS = 900;
const MIN_TURNS = 3;

interface ConversationScrollPeekProps {
  turns: ChatTurn[];
  chunkHeights: readonly number[];
  turnsPerChunk: number;
  scroller: HTMLElement | null;
  /** Bump when measured heights change so offsets recompute. */
  heightsTick: number;
}

/**
 * Codex-style conversation peek: right-edge ticks + a floating snippet of the
 * turn under the thumb. Own state so hover/scroll never re-renders the list.
 */
export function ConversationScrollPeek({
  turns,
  chunkHeights,
  turnsPerChunk,
  scroller,
  heightsTick,
}: ConversationScrollPeekProps) {
  const rootRef = useRef<HTMLDivElement>(null);
  const hideTimer = useRef(0);
  const hovering = useRef(false);
  const [open, setOpen] = useState(false);
  const [index, setIndex] = useState(0);
  const [anchorY, setAnchorY] = useState(0);
  const [overflow, setOverflow] = useState(false);

  const offsets = useMemo(
    () => turnOffsetsFromChunkHeights(chunkHeights, turnsPerChunk, turns.length),
    // heightsTick is the signal that chunkHeights contents changed (same array ref).
    [chunkHeights, turnsPerChunk, turns.length, heightsTick],
  );

  const show = useCallback((i: number, y: number) => {
    if (hideTimer.current) window.clearTimeout(hideTimer.current);
    hideTimer.current = 0;
    setIndex(i);
    setAnchorY(y);
    setOpen(true);
  }, []);

  const scheduleHide = useCallback(() => {
    if (hovering.current) return;
    if (hideTimer.current) window.clearTimeout(hideTimer.current);
    hideTimer.current = window.setTimeout(() => {
      hideTimer.current = 0;
      setOpen(false);
    }, HIDE_AFTER_MS);
  }, []);

  const indexFromClientY = useCallback(
    (clientY: number): { i: number; y: number } | null => {
      const track = rootRef.current?.querySelector<HTMLElement>("[data-chat-scroll-peek-track]");
      const root = rootRef.current;
      if (!track || !root || turns.length === 0) return null;
      const trackRect = track.getBoundingClientRect();
      const rootRect = root.getBoundingClientRect();
      const frac = (clientY - trackRect.top) / Math.max(1, trackRect.height);
      const i = indexAtFraction(frac, turns.length);
      if (i < 0) return null;
      return { i, y: clientY - rootRect.top };
    },
    [turns.length],
  );

  useEffect(() => {
    if (!scroller) return;
    const measure = () => {
      setOverflow(scroller.scrollHeight > scroller.clientHeight + 32);
    };
    measure();
    const onScroll = () => {
      measure();
      if (turns.length < MIN_TURNS) return;
      const max = scroller.scrollHeight - scroller.clientHeight;
      const frac = max <= 0 ? 1 : scroller.scrollTop / max;
      // Auto-follow at the tail should not pop the peek on every streamed token.
      if (frac > 0.97 && !hovering.current) {
        scheduleHide();
        return;
      }
      const i = indexAtFraction(frac, turns.length);
      const root = rootRef.current;
      const track = root?.querySelector<HTMLElement>("[data-chat-scroll-peek-track]");
      if (i < 0 || !root || !track) return;
      const trackRect = track.getBoundingClientRect();
      const rootRect = root.getBoundingClientRect();
      show(i, trackRect.top - rootRect.top + frac * trackRect.height);
      if (!hovering.current) scheduleHide();
    };
    scroller.addEventListener("scroll", onScroll, { passive: true });
    let ro: ResizeObserver | undefined;
    if (typeof ResizeObserver !== "undefined") {
      ro = new ResizeObserver(measure);
      ro.observe(scroller);
    }
    return () => {
      scroller.removeEventListener("scroll", onScroll);
      ro?.disconnect();
    };
  }, [scroller, turns.length, show, scheduleHide]);

  useEffect(() => () => {
    if (hideTimer.current) window.clearTimeout(hideTimer.current);
  }, []);

  const jumpTo = useCallback(
    (i: number) => {
      if (!scroller || i < 0 || i >= offsets.length) return;
      scroller.scrollTo({ top: offsets[i], behavior: "auto" });
    },
    [scroller, offsets],
  );

  if (turns.length < MIN_TURNS) return null;

  const turn = turns[index];
  const lines = turn ? peekLinesForTurn(turn) : { query: "", reply: "", more: "" };
  const ticks = visibleTickIndexes(turns.length, 400, index);
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
        onPointerEnter={(e) => {
          hovering.current = true;
          const hit = indexFromClientY(e.clientY);
          if (hit) show(hit.i, hit.y);
        }}
        onPointerMove={(e) => {
          const hit = indexFromClientY(e.clientY);
          if (hit) show(hit.i, hit.y);
        }}
        onPointerLeave={() => {
          hovering.current = false;
          scheduleHide();
        }}
        onClick={(e) => {
          const hit = indexFromClientY(e.clientY);
          if (!hit) return;
          show(hit.i, hit.y);
          jumpTo(hit.i);
        }}
      >
        {ticks.map((i) => (
          <span
            key={turns[i]?.id ?? i}
            className={`chat-scroll-peek-tick${i === index && open ? " is-active" : ""}`}
            style={{ top: `${(i / Math.max(1, turns.length - 1)) * 100}%` }}
          />
        ))}
      </div>
      {showCard ? (
        <div
          className="chat-scroll-peek-float"
          style={{ ["--peek-y" as string]: `${anchorY}px` }}
          data-chat-scroll-peek-card
        >
          <span className="chat-scroll-peek-line" />
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
