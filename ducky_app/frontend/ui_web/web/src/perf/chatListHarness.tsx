/**
 * Chat list perf harness (dev only): mounts the real VirtualChatMessageList with
 * the real row components + app CSS on a real exported chat, then streams tokens
 * and scrolls while recording commit time, frame time and long tasks.
 *
 *   npm run dev  →  http://localhost:5173/perf-chat.html?rows=/perf/big-chat-rows.json
 *   window.__chatPerf.run()  →  JSON stats (also shown in the top bar)
 */
import { useLayoutEffect, useMemo, useRef, useState } from "react";
import { flushSync } from "react-dom";
import ReactDOM from "react-dom/client";
import "../theme/styles/index.css";

import { VirtualChatMessageList } from "../components/VirtualChatMessageList";
import {
  appendStreamRow,
  buildChatRows,
  coalesceActivityRows,
  type ChatRow,
} from "../utils/chatMessageGroups";
import type { ChatMessage } from "../types/panel";
import { syntheticMessages } from "./syntheticChat";

type PerfEntry = Record<string, unknown>;

const perfEntries: PerfEntry[] = [];

// Fake panel API: satisfies isPanelApiReady() and captures report_ui_perf so the
// list's own commit probe lands here instead of the Python perf log.
(window as unknown as { pywebview: unknown }).pywebview = {
  api: new Proxy(
    {},
    {
      get: (_t, key) => {
        if (key === "report_ui_perf") {
          return async (entries: PerfEntry[]) => {
            perfEntries.push(...entries);
            return true;
          };
        }
        if (key === "get_listener_status") return async () => ({ ok: true });
        return async () => null;
      },
    },
  ),
};

const noop = () => {};
// Stable like ChatPane's memoized state: a fresh [] per render would change the
// list's row-environment context every frame and re-render every row.
const EMPTY: never[] = [];

function lastUserId(rows: ChatRow[]): string | null {
  for (let i = rows.length - 1; i >= 0; i--) {
    const r = rows[i];
    if (r.kind === "bubble" && r.role === "user") return r.id;
  }
  return null;
}

interface Stats {
  rows: number;
  mountMs: number;
  domNodes: number;
  stream: { frames: number; commitAvgMs: number; commitMaxMs: number; frameAvgMs: number; frameMaxMs: number; longTasks: number; longTaskMs: number };
  scroll: { steps: number; frameAvgMs: number; frameMaxMs: number; longTasks: number; longTaskMs: number };
  probeReports: PerfEntry[];
  /** Frames measured on the timer fallback (tab hidden); paint not included there. */
  hiddenFrames: number;
}

let setStreamText: (t: string) => void = noop;
let committedRows: ChatRow[] = [];
let mountMs = 0;

function Harness({ committed }: { committed: ChatRow[] }) {
  const [streamText, setText] = useState("");
  setStreamText = setText;
  const t0 = useRef(performance.now());
  useLayoutEffect(() => {
    mountMs = performance.now() - t0.current;
  }, []);
  const rows = useMemo(
    () => (streamText ? coalesceActivityRows(appendStreamRow(committed, streamText, true)) : committed),
    [committed, streamText],
  );
  const editableRowId = useMemo(() => lastUserId(committed), [committed]);
  return (
    <VirtualChatMessageList
      rows={rows}
      showActivityPanel={false}
      activityHeaderOnly={false}
      isWaitingOnLinked={false}
      waitingLinked={EMPTY}
      isAtBottom
      hasNewBelow={false}
      editableRowId={editableRowId}
      composerMode="agent"
      composerModel="perf"
      composerCodingAgent="ducky"
      setComposerCodingAgent={noop}
      convId="perf"
      onResend={noop}
      onAtBottomChange={noop}
      onJumpToLatest={noop}
      onOpenChat={noop}
      onStopLinked={noop}
      allChats={EMPTY}
      linkedAgents={EMPTY}
      activityLines={EMPTY}
      chatPlan={null}
      chatPlanProgress={null}
      planAllDone={false}
    />
  );
}

function longTaskRecorder() {
  let count = 0;
  let total = 0;
  let observer: PerformanceObserver | null = null;
  try {
    observer = new PerformanceObserver((list) => {
      for (const e of list.getEntries()) {
        count++;
        total += e.duration;
      }
    });
    observer.observe({ type: "longtask", buffered: false });
  } catch {
    observer = null;
  }
  return {
    stop: () => {
      observer?.disconnect();
      return { count, total: Math.round(total) };
    },
  };
}

/** Frame clock. In a hidden tab rAF never fires, so fall back to a timer and
 *  force style+layout by reading the scroller's scrollHeight (paint excluded). */
let hiddenFrames = 0;
const nextFrame = () =>
  new Promise<number>((resolve) => {
    let done = false;
    const raf = requestAnimationFrame((t) => {
      if (done) return;
      done = true;
      window.clearTimeout(timer);
      resolve(t);
    });
    const timer = window.setTimeout(() => {
      if (done) return;
      done = true;
      cancelAnimationFrame(raf);
      hiddenFrames++;
      const scroller = document.querySelector<HTMLElement>(".virtual-chat-message-list-scroller");
      void scroller?.scrollHeight;
      resolve(performance.now());
    }, 40);
  });

async function streamRun(frames: number, tokensPerFrame: number) {
  const commits: number[] = [];
  const frameTimes: number[] = [];
  const lt = longTaskRecorder();
  let text = "";
  let last = await nextFrame();
  for (let i = 0; i < frames; i++) {
    for (let k = 0; k < tokensPerFrame; k++) text += "streamed token ";
    const c0 = performance.now();
    flushSync(() => setStreamText(text));
    commits.push(performance.now() - c0);
    const now = await nextFrame();
    frameTimes.push(now - last);
    last = now;
  }
  const l = lt.stop();
  flushSync(() => setStreamText(""));
  const avg = (a: number[]) => a.reduce((s, v) => s + v, 0) / Math.max(1, a.length);
  return {
    frames,
    commitAvgMs: +avg(commits).toFixed(2),
    commitMaxMs: +Math.max(...commits).toFixed(2),
    frameAvgMs: +avg(frameTimes).toFixed(2),
    frameMaxMs: +Math.max(...frameTimes).toFixed(2),
    longTasks: l.count,
    longTaskMs: l.total,
  };
}

async function scrollRun(steps: number) {
  const scroller = document.querySelector<HTMLElement>(".virtual-chat-message-list-scroller");
  if (!scroller) throw new Error("scroller missing");
  const frameTimes: number[] = [];
  const lt = longTaskRecorder();
  scroller.scrollTop = 0;
  let last = await nextFrame();
  const max = scroller.scrollHeight - scroller.clientHeight;
  for (let i = 1; i <= steps; i++) {
    scroller.scrollTop = Math.round((max * i) / steps);
    const now = await nextFrame();
    frameTimes.push(now - last);
    last = now;
  }
  const l = lt.stop();
  const avg = (a: number[]) => a.reduce((s, v) => s + v, 0) / Math.max(1, a.length);
  return {
    steps,
    frameAvgMs: +avg(frameTimes).toFixed(2),
    frameMaxMs: +Math.max(...frameTimes).toFixed(2),
    longTasks: l.count,
    longTaskMs: l.total,
  };
}

async function run(opts: { frames?: number; tokensPerFrame?: number; scrollSteps?: number } = {}): Promise<Stats> {
  const stream = await streamRun(opts.frames ?? 120, opts.tokensPerFrame ?? 3);
  const scroll = await scrollRun(opts.scrollSteps ?? 80);
  const stats: Stats = {
    rows: committedRows.length,
    mountMs: +mountMs.toFixed(1),
    domNodes: document.querySelectorAll("*").length,
    stream,
    scroll,
    probeReports: perfEntries.filter((e) => e.name === "chat_list_commit"),
    hiddenFrames,
  };
  const bar = document.getElementById("perf-bar");
  if (bar) bar.textContent = JSON.stringify({ ...stats, probeReports: stats.probeReports.length }, null, 0);
  return stats;
}

async function main() {
  const params = new URLSearchParams(location.search);
  const synthetic = Number(params.get("synthetic") ?? 0);
  const src = params.get("rows") ?? "/perf/big-chat-rows.json";
  const messages = synthetic > 0 ? syntheticMessages(synthetic) : ((await (await fetch(src)).json()) as ChatMessage[]);
  committedRows = buildChatRows({ committed: messages, turnMessages: [], streamBuffer: "", inFlight: false });
  const bar = document.getElementById("perf-bar");
  const t0 = performance.now();
  const root = ReactDOM.createRoot(document.getElementById("perf-list")!);
  flushSync(() => root.render(<Harness committed={committedRows} />));
  await nextFrame();
  const firstFrameMs = performance.now() - t0;
  const chunksAtFirstFrame = document.querySelectorAll(".virtual-chat-chunk").length;
  if (bar) bar.textContent = `${messages.length} messages → ${committedRows.length} rows; first frame ${firstFrameMs.toFixed(1)} ms. Run window.__chatPerf.run()`;
  (window as unknown as { __chatPerf: unknown }).__chatPerf = { run, streamRun, scrollRun, entries: perfEntries, firstFrameMs, chunksAtFirstFrame };
}

void main();
