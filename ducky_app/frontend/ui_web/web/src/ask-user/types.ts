export type AskUserOption = {
  id: string;
  label: string;
  description?: string;
};

export type AskUserQuestion = {
  id: string;
  prompt: string;
  options: AskUserOption[];
  allow_multiple: boolean;
  allow_free_text: boolean;
  required: boolean;
  /** Verbatim text shown in a code block (an approval card's command or path). */
  detail?: string;
  /** Caution line above the options (e.g. "Pushes commits to a remote."). */
  warning?: string;
};

export type AskUserAnswer = {
  selected: string[];
  text: string;
  skipped: boolean;
};

export type AskUserResult = {
  ok: true;
  answers: Record<string, AskUserAnswer>;
  skipped_all: boolean;
  /** The person typed a chat message instead of using the card: their own words. */
  free_text?: string;
  /** The person pressed Cancel: they won't answer, so the agent stops instead of guessing. */
  cancelled?: boolean;
};

/** A file attached to a written answer, already saved in the asking chat. */
export type AskUserFile = { name: string; path: string; readablePath?: string };

export type AskUserDraft = {
  selected: string[];
  text: string;
  other: boolean;
  files?: AskUserFile[];
};

export function emptyDraft(): AskUserDraft {
  return { selected: [], text: "", other: false };
}

export function canSubmitQuestion(question: AskUserQuestion, draft: AskUserDraft): boolean {
  if (!question.required) return true;
  if (draft.other || (!question.options.length && question.allow_free_text)) {
    return draft.text.trim().length > 0 || Boolean(draft.files?.length);
  }
  return draft.selected.length > 0;
}

/** The written answer, with where each attached file was saved for the agent to read. */
function answerText(draft: AskUserDraft): string {
  const files = (draft.files ?? []).map((f) =>
    `Attached file: ${f.name}, saved at ${f.path}${f.readablePath ? ` (readable copy: ${f.readablePath})` : ""}. `
    + "Read it with workspace_read_file.");
  return [draft.text.trim(), ...files].filter(Boolean).join("\n");
}

export function draftToAnswer(draft: AskUserDraft, skipped = false): AskUserAnswer {
  if (skipped) {
    return { selected: [], text: "", skipped: true };
  }
  if (draft.other) {
    return { selected: [], text: answerText(draft), skipped: false };
  }
  return {
    selected: [...draft.selected],
    text: answerText(draft),
    skipped: false,
  };
}

export function parseAskUserQuestions(raw: unknown): AskUserQuestion[] {
  if (!Array.isArray(raw)) return [];
  const out: AskUserQuestion[] = [];
  for (const row of raw) {
    if (!row || typeof row !== "object") continue;
    const q = row as Record<string, unknown>;
    const id = String(q.id || "").trim();
    const prompt = String(q.prompt || "").trim();
    if (!id || !prompt) continue;
    const optionsRaw = Array.isArray(q.options) ? q.options : [];
    const options: AskUserOption[] = [];
    for (const opt of optionsRaw) {
      if (!opt || typeof opt !== "object") continue;
      const o = opt as Record<string, unknown>;
      const oid = String(o.id || "").trim();
      const label = String(o.label || "").trim();
      if (!oid || !label) continue;
      options.push({
        id: oid,
        label,
        description: String(o.description || "").trim(),
      });
    }
    const detail = typeof q.detail === "string" ? q.detail.trimEnd() : "";
    const warning = typeof q.warning === "string" ? q.warning.trim() : "";
    out.push({
      id,
      prompt,
      options,
      allow_multiple: Boolean(q.allow_multiple),
      allow_free_text: q.allow_free_text !== false,
      required: q.required !== false,
      ...(detail ? { detail } : {}),
      ...(warning ? { warning } : {}),
    });
  }
  return out;
}
