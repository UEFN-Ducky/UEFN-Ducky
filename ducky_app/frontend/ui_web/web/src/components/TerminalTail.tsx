import { useEffect, useRef, useState } from "react";

import { terminalText } from "../automations/TerminalOutput";
import { getApi } from "../hooks/usePanelApi";
import { requestOpenTerminalTab } from "../navigation/openTerminalTab";

export const TerminalGlyph = () => (
  <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden>
    <path d="M4 17l6-5-6-5" />
    <path d="M12 19h8" />
  </svg>
);

/** Terminal text a person reads: no colour codes, no Ducky's run wrapper (its echo and markers). */
export function tailText(output: string): string {
  return terminalText(output)
    .split("\n")
    .filter((line) => !line.includes("__DUCKY_") && !line.includes("$__duckyWas"))
    .join("\n")
    .replace(/\n+$/, "");
}

/** A terminal's output folding open under its row; Open terminal shows the session's tab. */
export function TerminalTail({
  output,
  open,
  live,
  sessionId = "",
  label = "Terminal",
}: {
  output: string;
  open: boolean;
  live: boolean;
  sessionId?: string;
  label?: string;
}) {
  const ref = useRef<HTMLPreElement | null>(null);
  const follow = useRef(true);
  const text = tailText(output);
  useEffect(() => {
    if (open && ref.current && follow.current) ref.current.scrollTop = ref.current.scrollHeight;
  }, [open, text]);
  return (
    <div className={`chat-workflow-run-term${open ? " is-open" : ""}`} aria-hidden={!open}>
      <div className="chat-workflow-run-term-inner">
        <div className="chat-workflow-run-term-head">
          <span className={`chat-workflow-run-term-live${live ? " is-live" : ""}`} />
          <span>{live ? "Live output" : "Output"}</span>
          {sessionId ? (
            <button
              type="button"
              className="chat-workflow-run-term-open"
              tabIndex={open ? 0 : -1}
              onClick={() => requestOpenTerminalTab(sessionId)}
              title="Open this terminal in a tab"
            >
              <TerminalGlyph />
              Open terminal
            </button>
          ) : null}
        </div>
        <pre
          ref={ref}
          aria-label={`${label} output`}
          onScroll={(event) => {
            const el = event.currentTarget;
            follow.current = el.scrollHeight - el.scrollTop - el.clientHeight < 24;
          }}
        >
          {text || "Waiting for output…"}
        </pre>
      </div>
    </div>
  );
}

const POLL_MS = 700;

/** While a terminal command runs, show what it prints (polled), never just a spinner. */
export function LiveTerminalTail({ sessionId }: { sessionId: string }) {
  const [output, setOutput] = useState("");
  useEffect(() => {
    let stopped = false;
    let timer = 0;
    const poll = async () => {
      try {
        const res = await getApi()?.terminal_read_output(sessionId, 8000);
        if (!stopped && res) setOutput(String(res.output ?? ""));
      } catch {
        // the next poll tries again
      }
      if (!stopped) timer = window.setTimeout(() => void poll(), POLL_MS);
    };
    void poll();
    return () => {
      stopped = true;
      window.clearTimeout(timer);
    };
  }, [sessionId]);
  return (
    <div className="tool-execution-card-terminal-tail">
      <TerminalTail output={output} open live sessionId={sessionId} />
    </div>
  );
}
