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
};

export type AskUserDraft = {
  selected: string[];
  text: string;
  other: boolean;
};

export function emptyDraft(): AskUserDraft {
  return { selected: [], text: "", other: false };
}

export function canSubmitQuestion(question: AskUserQuestion, draft: AskUserDraft): boolean {
  if (!question.required) return true;
  if (draft.other || (!question.options.length && question.allow_free_text)) {
    return draft.text.trim().length > 0;
  }
  return draft.selected.length > 0;
}

export function draftToAnswer(draft: AskUserDraft, skipped = false): AskUserAnswer {
  if (skipped) {
    return { selected: [], text: "", skipped: true };
  }
  if (draft.other) {
    return { selected: [], text: draft.text.trim(), skipped: false };
  }
  return {
    selected: [...draft.selected],
    text: draft.text.trim(),
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
    out.push({
      id,
      prompt,
      options,
      allow_multiple: Boolean(q.allow_multiple),
      allow_free_text: q.allow_free_text !== false,
      required: q.required !== false,
    });
  }
  return out;
}
