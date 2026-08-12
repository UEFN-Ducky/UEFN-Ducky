import { useEffect, useMemo, useState } from "react";

import {
  canSubmitQuestion,
  draftToAnswer,
  emptyDraft,
  type AskUserAnswer,
  type AskUserDraft,
  type AskUserQuestion,
  type AskUserResult,
} from "./types";

type Props = {
  questions: AskUserQuestion[];
  title: string;
  queueAhead?: number;
  /** When false, number/Enter shortcuts are not bound (other chats may be asking too). */
  captureKeys?: boolean;
  /** Inline chat card shows ×; modal uses its own floating close. */
  showDismiss?: boolean;
  onComplete: (result: AskUserResult) => void;
};

function OptionControl({
  multiple,
  selected,
}: {
  multiple: boolean;
  selected: boolean;
}) {
  return (
    <span
      className={`ask-user-control ask-user-control--${multiple ? "checkbox" : "radio"}${
        selected ? " is-checked" : ""
      }`}
      aria-hidden="true"
    />
  );
}

function draftSummary(question: AskUserQuestion, draft: AskUserDraft): string {
  if (draft.other || (!question.options.length && question.allow_free_text)) {
    const text = draft.text.trim();
    return text || "…";
  }
  if (!draft.selected.length) return "";
  const byId = new Map(question.options.map((o) => [o.id, o.label]));
  return draft.selected.map((id) => byId.get(id) || id).join(", ");
}

export function AskUserForm({
  questions,
  title,
  queueAhead = 0,
  captureKeys = true,
  showDismiss = true,
  onComplete,
}: Props) {
  const [index, setIndex] = useState(0);
  const [drafts, setDrafts] = useState<AskUserDraft[]>(() => questions.map(() => emptyDraft()));
  /** Questions the user has explicitly Submitted (not just drafted). */
  const [committed, setCommitted] = useState<boolean[]>(() => questions.map(() => false));

  useEffect(() => {
    setIndex(0);
    setDrafts(questions.map(() => emptyDraft()));
    setCommitted(questions.map(() => false));
  }, [questions]);

  const question = questions[index] ?? null;
  const draft = drafts[index] ?? emptyDraft();
  const total = questions.length;
  const canSubmit = question ? canSubmitQuestion(question, draft) : false;
  const multiple = Boolean(question?.allow_multiple);
  const isLast = index + 1 >= total;
  const canSkip = Boolean(question && !question.required);

  const answersSoFar = useMemo(() => {
    const out: Record<string, AskUserAnswer> = {};
    for (let i = 0; i < drafts.length && i < questions.length; i++) {
      if (committed[i]) {
        out[questions[i].id] = draftToAnswer(drafts[i]);
      }
    }
    return out;
  }, [drafts, questions, committed]);

  const setDraft = (patch: Partial<AskUserDraft>) => {
    setDrafts((prev) => {
      const next = [...prev];
      next[index] = { ...(next[index] ?? emptyDraft()), ...patch };
      return next;
    });
  };

  const finish = (answers: Record<string, AskUserAnswer>) => {
    const skipped_all = Object.values(answers).every((a) => a.skipped);
    onComplete({ ok: true, answers, skipped_all });
  };

  const handleSubmit = () => {
    if (!question || !canSubmit) return;
    const nextDrafts = [...drafts];
    nextDrafts[index] = draft;
    const nextCommitted = [...committed];
    nextCommitted[index] = true;
    setDrafts(nextDrafts);
    setCommitted(nextCommitted);

    const answers = { ...answersSoFar, [question.id]: draftToAnswer(draft) };
    if (isLast) {
      // Fill any never-reached optional slots as skipped (shouldn't happen in order).
      for (let i = 0; i < questions.length; i++) {
        if (!nextCommitted[i] && i !== index) {
          answers[questions[i].id] = { selected: [], text: "", skipped: true };
        }
      }
      finish(answers);
      return;
    }
    setIndex((i) => i + 1);
  };

  const handleSkip = () => {
    if (!question || !canSkip) return;
    const nextDrafts = [...drafts];
    nextDrafts[index] = emptyDraft();
    const nextCommitted = [...committed];
    nextCommitted[index] = true;
    setDrafts(nextDrafts);
    setCommitted(nextCommitted);

    const answers = {
      ...answersSoFar,
      [question.id]: draftToAnswer(emptyDraft(), true),
    };
    if (isLast) {
      for (let i = 0; i < questions.length; i++) {
        if (!nextCommitted[i] && i !== index) {
          answers[questions[i].id] = { selected: [], text: "", skipped: true };
        }
      }
      finish(answers);
      return;
    }
    setIndex((i) => i + 1);
  };

  const handleBack = () => {
    if (index <= 0) return;
    setIndex((i) => i - 1);
  };

  const handleClose = () => {
    const answers: Record<string, AskUserAnswer> = { ...answersSoFar };
    for (let i = 0; i < questions.length; i++) {
      if (!answers[questions[i].id]) {
        answers[questions[i].id] = { selected: [], text: "", skipped: true };
      }
    }
    finish(answers);
  };

  const toggleOption = (optionId: string) => {
    if (!question) return;
    if (question.allow_multiple) {
      const selected = draft.selected.includes(optionId)
        ? draft.selected.filter((id) => id !== optionId)
        : [...draft.selected, optionId];
      setDraft({ selected, other: false });
      return;
    }
    // Selection alone never continues the agent — only Submit does.
    setDraft({ selected: [optionId], other: false, text: "" });
  };

  useEffect(() => {
    if (!captureKeys || !question) return;
    const onKey = (e: KeyboardEvent) => {
      const target = e.target as HTMLElement | null;
      const typing =
        target &&
        (target.tagName === "INPUT" ||
          target.tagName === "TEXTAREA" ||
          target.isContentEditable);
      if (e.key === "Enter" && !e.shiftKey && !typing && canSubmit) {
        e.preventDefault();
        handleSubmit();
        return;
      }
      if (typing) return;
      const n = Number(e.key);
      if (n >= 1 && n <= 9) {
        const opt = question.options[n - 1];
        if (opt) {
          e.preventDefault();
          toggleOption(opt.id);
        } else if (
          question.allow_free_text &&
          question.options.length &&
          n === question.options.length + 1
        ) {
          e.preventDefault();
          setDraft({ other: true, selected: [] });
        }
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
    // eslint-disable-next-line react-hooks/exhaustive-deps -- handlers close over latest draft
  }, [captureKeys, question, draft, canSubmit, index, isLast]);

  if (!question) return null;

  const showOther = Boolean(question.allow_free_text && question.options.length > 0);
  const freeTextOnly = Boolean(!question.options.length && question.allow_free_text);

  return (
    <div className="ask-user-body">
      <div className="ask-user-top">
        <span className="ask-user-badge" aria-label={`Question ${index + 1} of ${total}`}>
          {index + 1}/{total}
        </span>
        {queueAhead > 0 ? <span className="ask-user-queue">+{queueAhead} waiting</span> : null}
        <span className="ask-user-pause-hint">Agent paused until Submit</span>
        {showDismiss ? (
          <button
            type="button"
            className="ask-user-dismiss"
            aria-label="Dismiss"
            onClick={handleClose}
          >
            ×
          </button>
        ) : null}
      </div>
      {title ? <p className="ask-user-session-title">{title}</p> : null}

      {total > 1 ? (
        <ol className="ask-user-batch" aria-label="All questions">
          {questions.map((q, i) => {
            const done = committed[i];
            const current = i === index;
            const summary = done
              ? draftToAnswer(drafts[i]).skipped
                ? "Skipped"
                : draftSummary(q, drafts[i]) || "Answered"
              : current
                ? "Answering…"
                : "Not answered";
            return (
              <li
                key={q.id}
                className={[
                  "ask-user-batch-item",
                  done ? "is-done" : "",
                  current ? "is-current" : "",
                  done && draftToAnswer(drafts[i]).skipped ? "is-skipped" : "",
                ]
                  .filter(Boolean)
                  .join(" ")}
              >
                <span className="ask-user-batch-prompt">{q.prompt}</span>
                <span className="ask-user-batch-status">{summary}</span>
              </li>
            );
          })}
        </ol>
      ) : null}

      <h2 className="ask-user-prompt">{question.prompt}</h2>
      <div
        className="ask-user-options"
        role={multiple ? "group" : "radiogroup"}
        aria-label="Options"
        aria-multiselectable={multiple || undefined}
      >
        {question.options.map((opt, i) => {
          const selected = draft.selected.includes(opt.id) && !draft.other;
          return (
            <button
              key={opt.id}
              type="button"
              role={multiple ? "checkbox" : "radio"}
              aria-checked={selected}
              className={`ask-user-option${selected ? " is-selected" : ""}`}
              onClick={() => toggleOption(opt.id)}
            >
              <OptionControl multiple={multiple} selected={selected} />
              <div className="ask-user-option-main">
                <span className="ask-user-option-label">{opt.label}</span>
                {opt.description ? (
                  <span className="ask-user-option-desc">{opt.description}</span>
                ) : null}
              </div>
              <span className="ask-user-option-key">{i + 1}</span>
            </button>
          );
        })}
        {showOther ? (
          <div className={`ask-user-option ask-user-other${draft.other ? " is-selected" : ""}`}>
            <button
              type="button"
              className="ask-user-other-toggle"
              role={multiple ? "checkbox" : "radio"}
              aria-checked={draft.other}
              onClick={() => setDraft({ other: true, selected: [] })}
            >
              <OptionControl multiple={multiple} selected={draft.other} />
              <span className="ask-user-option-label">Other</span>
              <span className="ask-user-option-key">{question.options.length + 1}</span>
            </button>
            {draft.other ? (
              <input
                className="ask-user-text"
                type="text"
                placeholder="Type your own answer here"
                value={draft.text}
                autoFocus
                onChange={(e) => setDraft({ text: e.target.value, other: true, selected: [] })}
                onKeyDown={(e) => {
                  if (
                    e.key === "Enter" &&
                    canSubmitQuestion(question, {
                      ...draft,
                      other: true,
                      text: (e.target as HTMLInputElement).value,
                    })
                  ) {
                    e.preventDefault();
                    handleSubmit();
                  }
                }}
              />
            ) : null}
          </div>
        ) : null}
        {freeTextOnly ? (
          <input
            className="ask-user-text ask-user-text--solo"
            type="text"
            placeholder="Type your answer here"
            value={draft.text}
            autoFocus
            onChange={(e) => setDraft({ text: e.target.value })}
            onKeyDown={(e) => {
              if (e.key === "Enter" && canSubmit) {
                e.preventDefault();
                handleSubmit();
              }
            }}
          />
        ) : null}
      </div>
      <div className="ask-user-footer">
        <button type="button" className="settings-btn" onClick={handleBack} disabled={index === 0}>
          Back
        </button>
        <div className="ask-user-footer-right">
          {canSkip ? (
            <button type="button" className="settings-btn" onClick={handleSkip}>
              Skip
            </button>
          ) : null}
          <button
            type="button"
            className={`settings-btn modal-confirm-btn${canSubmit ? "" : " is-disabled"}`}
            onClick={handleSubmit}
            disabled={!canSubmit}
          >
            {isLast ? "Submit Enter" : "Next Enter"}
          </button>
        </div>
      </div>
    </div>
  );
}
