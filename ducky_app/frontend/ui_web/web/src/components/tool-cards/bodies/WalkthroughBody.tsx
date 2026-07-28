import { useMemo, useState, type KeyboardEvent, type MouseEvent } from "react";
import { Icons } from "../../../icons/Icons";
import { runAgentWalkthrough } from "../../../walkthrough/agentWalkthrough";
import type { ToolCardBodyProps } from "../toolCardTypes";

function stepsFromArgs(args: Record<string, unknown>, resultText: string): unknown[] {
  if (Array.isArray(args.steps)) return args.steps;
  try {
    const parsed = JSON.parse(resultText) as { steps?: unknown };
    if (Array.isArray(parsed?.steps)) return parsed.steps;
  } catch {
    /* ignore */
  }
  return [];
}

function stepTitle(step: unknown, index: number): string {
  if (!step || typeof step !== "object") return `Step ${index + 1}`;
  const s = step as { title?: unknown; body?: unknown; target?: unknown };
  const title = String(s.title || "").trim();
  if (title) return title;
  const body = String(s.body || "").trim();
  if (body) return body.length > 48 ? `${body.slice(0, 45)}…` : body;
  return String(s.target || `Step ${index + 1}`);
}

export function WalkthroughBody({ args, resultText, isSuccess, isError, showResult }: ToolCardBodyProps) {
  const steps = useMemo(() => stepsFromArgs(args, resultText), [args, resultText]);
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState("");

  const replay = async (e?: MouseEvent | KeyboardEvent) => {
    e?.stopPropagation();
    if (!steps.length || busy) return;
    setBusy(true);
    setNote("");
    try {
      const out = await runAgentWalkthrough(steps);
      if (out.error) {
        setNote(String(out.error));
      } else if (out.skipped) {
        setNote("Skipped");
      } else if (out.completed) {
        setNote("Completed");
      }
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="tool-card-walkthrough-body">
      <div className="tool-card-walkthrough-toolbar">
        <button
          type="button"
          className="tool-card-walkthrough-replay"
          disabled={!steps.length || busy}
          onClick={(e) => void replay(e)}
          title="Replay this tutorial"
        >
          <Icons.Refresh />
          <span>{busy ? "Playing…" : "Replay tutorial"}</span>
        </button>
        {note ? <span className="tool-card-walkthrough-note">{note}</span> : null}
        {showResult && isSuccess && !note ? (
          <span className="tool-card-walkthrough-note">Saved in chat — replay anytime</span>
        ) : null}
        {showResult && isError ? (
          <span className="tool-card-walkthrough-note tool-card-walkthrough-note--error">Tour failed</span>
        ) : null}
      </div>
      {steps.length ? (
        <ol className="tool-card-walkthrough-steps">
          {steps.map((step, i) => (
            <li key={i}>{stepTitle(step, i)}</li>
          ))}
        </ol>
      ) : (
        <p className="tool-card-walkthrough-empty">No steps stored on this card.</p>
      )}
    </div>
  );
}

/** Extract steps from a tool card for header Replay (args first, then result). */
export function walkthroughStepsFromTool(meta: {
  arguments?: Record<string, unknown>;
  result?: unknown;
}): unknown[] | null {
  const args = meta.arguments || {};
  if (Array.isArray(args.steps) && args.steps.length) return args.steps;
  const result = meta.result;
  if (result && typeof result === "object" && Array.isArray((result as { steps?: unknown }).steps)) {
    const steps = (result as { steps: unknown[] }).steps;
    return steps.length ? steps : null;
  }
  if (typeof result === "string") {
    try {
      const parsed = JSON.parse(result) as { steps?: unknown };
      if (Array.isArray(parsed.steps) && parsed.steps.length) return parsed.steps;
    } catch {
      /* ignore */
    }
  }
  return null;
}
