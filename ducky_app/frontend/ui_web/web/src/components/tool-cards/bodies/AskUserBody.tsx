import { useMemo } from "react";
import type { ToolCardBodyProps } from "../toolCardTypes";

type AnswerRow = {
  id: string;
  prompt: string;
  summary: string;
};

function questionsFromArgs(args: Record<string, unknown>): Array<{ id: string; prompt: string }> {
  if (!Array.isArray(args.questions)) return [];
  const out: Array<{ id: string; prompt: string }> = [];
  for (const row of args.questions) {
    if (!row || typeof row !== "object") continue;
    const q = row as { id?: unknown; prompt?: unknown };
    const id = String(q.id || "").trim();
    const prompt = String(q.prompt || "").trim();
    if (!id) continue;
    out.push({ id, prompt: prompt || id });
  }
  return out;
}

function summarizeAnswer(raw: unknown): string {
  if (!raw || typeof raw !== "object") return "—";
  const a = raw as { selected?: unknown; text?: unknown; skipped?: unknown };
  if (a.skipped) return "Skipped";
  const selected = Array.isArray(a.selected)
    ? a.selected.map((x) => String(x)).filter(Boolean)
    : [];
  const text = String(a.text || "").trim();
  if (selected.length && text) return `${selected.join(", ")} · ${text}`;
  if (selected.length) return selected.join(", ");
  if (text) return text;
  return "—";
}

export function rowsFromAskUser(
  args: Record<string, unknown>,
  resultText: string,
): AnswerRow[] {
  const questions = questionsFromArgs(args);
  let answers: Record<string, unknown> = {};
  try {
    const parsed = JSON.parse(resultText) as {
      answers?: Record<string, unknown>;
      questions?: unknown;
    };
    if (parsed?.answers && typeof parsed.answers === "object") {
      answers = parsed.answers;
    }
    if (!questions.length && Array.isArray(parsed?.questions)) {
      for (const row of parsed.questions) {
        if (!row || typeof row !== "object") continue;
        const q = row as { id?: unknown; prompt?: unknown };
        const id = String(q.id || "").trim();
        if (!id) continue;
        questions.push({ id, prompt: String(q.prompt || id).trim() || id });
      }
    }
  } catch {
    /* ignore */
  }
  const ids = questions.length
    ? questions.map((q) => q.id)
    : Object.keys(answers);
  const promptById = new Map(questions.map((q) => [q.id, q.prompt]));
  return ids.map((id) => ({
    id,
    prompt: promptById.get(id) || id,
    summary: summarizeAnswer(answers[id]),
  }));
}

export function AskUserBody({ args, resultText, showResult }: ToolCardBodyProps) {
  const rows = useMemo(() => rowsFromAskUser(args, resultText), [args, resultText]);

  if (!showResult && !rows.length) {
    return <p className="tool-card-ask-user-empty">Waiting for answers…</p>;
  }

  return (
    <div className="tool-card-ask-user-body">
      {rows.length ? (
        <ul className="tool-card-ask-user-rows">
          {rows.map((row) => (
            <li key={row.id}>
              <span className="tool-card-ask-user-q">{row.prompt}</span>
              <span className="tool-card-ask-user-a">→ {row.summary}</span>
            </li>
          ))}
        </ul>
      ) : (
        <p className="tool-card-ask-user-empty">No answers stored on this card.</p>
      )}
    </div>
  );
}
