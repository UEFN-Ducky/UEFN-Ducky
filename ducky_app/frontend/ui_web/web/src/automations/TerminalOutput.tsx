import { useEffect, useRef } from "react";

export type TerminalSnapshot = { output: string; sessionId: string };

export function terminalText(output: string): string {
  return output.replace(/\x1b\][^\x07]*(?:\x07|\x1b\\)/g, "")
    .replace(/\x1b\[[0-?]*[ -/]*[@-~]/g, "").replace(/\r\n/g, "\n").replace(/\r/g, "\n");
}

export function TerminalOutput({ snapshot, compact = false, title = "Terminal output" }: { snapshot: TerminalSnapshot; compact?: boolean; title?: string }) {
  const ref = useRef<HTMLPreElement>(null);
  const follow = useRef(true);
  useEffect(() => {
    if (ref.current && follow.current) ref.current.scrollTop = ref.current.scrollHeight;
  }, [snapshot.output]);
  return <section className={`aw-terminal-output${compact ? " is-compact" : ""}`} aria-label={title}
    onPointerDown={(event) => event.stopPropagation()} onDoubleClick={(event) => event.stopPropagation()}>
    <strong>{title}</strong>
    <pre ref={ref} tabIndex={0} onScroll={(event) => {
      const el = event.currentTarget;
      follow.current = el.scrollHeight - el.scrollTop - el.clientHeight < 24;
    }}>{terminalText(snapshot.output) || "Waiting for terminal output…"}</pre>
  </section>;
}
