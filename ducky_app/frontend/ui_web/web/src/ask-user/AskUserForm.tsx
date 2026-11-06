import { memo, useEffect, useMemo, useRef, useState, type DragEvent } from "react";

import {
  canSubmitQuestion,
  draftToAnswer,
  emptyDraft,
  type AskUserAnswer,
  type AskUserDraft,
  type AskUserFile,
  type AskUserQuestion,
  type AskUserResult,
} from "./types";
import type { MessageAuthorDto } from "../types/panel";
import { Icons } from "../icons/Icons";
import { getApi } from "../hooks/usePanelApi";

type Props = {
  questions: AskUserQuestion[];
  title: string;
  queueAhead?: number;
  author?: MessageAuthorDto;
  /** When false, number/Enter shortcuts are not bound (other chats may be asking too). */
  captureKeys?: boolean;
  /** Inline chat card shows × (only when every question is optional). */
  showDismiss?: boolean;
  /** The asking chat: files attached to a written answer are saved there for its agent. */
  chatId?: string;
  /** The asking agent's turn already ended: Submit sends the answer to its chat. */
  late?: boolean;
  onComplete: (result: AskUserResult) => void;
};

function readAsDataUrl(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result ?? ""));
    reader.onerror = () => reject(reader.error ?? new Error("Could not read the file"));
    reader.readAsDataURL(file);
  });
}

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
    >
      {multiple && selected ? <Icons.Check /> : null}
    </span>
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

export const AskUserForm = memo(function AskUserForm({
  questions,
  title,
  queueAhead = 0,
  author,
  captureKeys = true,
  showDismiss = true,
  chatId = "",
  late = false,
  onComplete,
}: Props) {
  const [index, setIndex] = useState(0);
  const [attaching, setAttaching] = useState(false);
  const [attachError, setAttachError] = useState("");
  const filePickerRef = useRef<HTMLInputElement>(null);
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
    if (questions.some((q, i) => q.required && !committed[i])) return;
    const answers: Record<string, AskUserAnswer> = { ...answersSoFar };
    for (let i = 0; i < questions.length; i++) {
      if (!answers[questions[i].id]) {
        answers[questions[i].id] = { selected: [], text: "", skipped: true };
      }
    }
    finish(answers);
  };

  /** Cancel: the person won't answer. Every question is skipped and the agent is told to stop. */
  const handleCancel = () => {
    const answers: Record<string, AskUserAnswer> = {};
    for (const q of questions) answers[q.id] = { selected: [], text: "", skipped: true };
    onComplete({ ok: true, answers, skipped_all: true, cancelled: true });
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

  }, [captureKeys, question, draft, canSubmit, index, isLast]);

  if (!question) return null;

  const showOther = Boolean(question.allow_free_text && question.options.length > 0);
  const freeTextOnly = Boolean(!question.options.length && question.allow_free_text);
  // A written answer can carry files: Ducky saves them in the asking chat and the answer
  // tells the agent where. The button lives in the footer; attaching picks "Other".
  const canAttach = Boolean(chatId && getApi()?.save_chat_file && (freeTextOnly || showOther));

  const attachFiles = async (files: FileList | File[] | null) => {
    const list = Array.from(files ?? []);
    const api = getApi();
    if (!list.length || !api?.save_chat_file) return;
    setAttaching(true);
    setAttachError("");
    const saved: AskUserFile[] = [];
    try {
      for (const file of list) {
        const res = await api.save_chat_file(chatId, file.name, await readAsDataUrl(file));
        if (!res?.ok || !res.path) {
          setAttachError(res?.error || `Could not save ${file.name}`);
          continue;
        }
        saved.push({ name: file.name, path: res.path, ...(res.readable_path ? { readablePath: res.readable_path } : {}) });
      }
    } catch (error) {
      setAttachError(error instanceof Error ? error.message : "Could not save the file");
    } finally {
      setAttaching(false);
    }
    if (!saved.length) return;
    const at = index;
    setDrafts((prev) => {
      const next = [...prev];
      const cur = next[at] ?? emptyDraft();
      next[at] = { ...cur, files: [...(cur.files ?? []), ...saved], ...(showOther ? { other: true, selected: [] } : {}) };
      return next;
    });
  };
  const removeFile = (path: string) => setDraft({ files: (draft.files ?? []).filter((f) => f.path !== path) });
  const onDropFiles = (event: DragEvent<HTMLDivElement>) => {
    if (!canAttach || !event.dataTransfer?.files?.length) return;
    event.preventDefault();
    void attachFiles(event.dataTransfer.files);
  };

  return (
    <div
      className="ask-user-body"
      onDragOver={canAttach ? (event) => event.preventDefault() : undefined}
      onDrop={canAttach ? onDropFiles : undefined}
    >
      <div className="ask-user-top">
        <span className="ask-user-badge" aria-hidden="true">
          <Icons.Help />
        </span>
        {author?.name?.trim() ? (
          <span
            className="ask-user-author"
            style={{ ["--member-color" as string]: author.color || "var(--accent)" }}
          >
            {author.name.trim()}
          </span>
        ) : (
          <span className="ask-user-heading">Question</span>
        )}
        {total > 1 ? (
          <span className="ask-user-count" aria-label={`Question ${index + 1} of ${total}`}>
            {index + 1}/{total}
          </span>
        ) : null}
        {queueAhead > 0 ? <span className="ask-user-queue">+{queueAhead} waiting</span> : null}
        <span className="ask-user-pause-hint" title={late ? "The agent moved on: Submit sends your answer to its chat" : "The agent waits until you Submit"}>
          <Icons.Pause />
          {late ? "Agent moved on: Submit sends to its chat" : "Agent paused"}
        </span>
        {showDismiss && questions.every((q) => !q.required) ? (
          <button type="button" className="icon-btn ask-user-dismiss" aria-label="Dismiss" title="Dismiss" onClick={handleClose}>
            <Icons.Close />
          </button>
        ) : null}
      </div>
      {title ? <p className="ask-user-session-title">{title}</p> : null}

      {total > 1 ? (
        <ol className="ask-user-batch" aria-label="All questions">
          {questions.map((q, i) => {
            const done = committed[i];
            const current = i === index;
            const skipped = done && draftToAnswer(drafts[i]).skipped;
            const summary = done ? (skipped ? "Skipped" : draftSummary(q, drafts[i]) || "Answered") : "";
            return (
              <li
                key={q.id}
                className={[
                  "ask-user-batch-item",
                  done ? "is-done" : "",
                  current ? "is-current" : "",
                  skipped ? "is-skipped" : "",
                ]
                  .filter(Boolean)
                  .join(" ")}
              >
                <span className="ask-user-batch-mark" aria-hidden="true">
                  {done && !skipped ? <Icons.Check /> : null}
                </span>
                <span className="ask-user-batch-prompt">{q.prompt}</span>
                {summary ? <span className="ask-user-batch-status">{summary}</span> : null}
              </li>
            );
          })}
        </ol>
      ) : null}

      <h2 className="ask-user-prompt">{question.prompt}</h2>
      {question.warning ? (
        <p className="ask-user-warning" role="note">
          <Icons.AlertTriangle />
          <span>{question.warning}</span>
        </p>
      ) : null}
      {question.detail ? <pre className="ask-user-detail">{question.detail}</pre> : null}
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
              <kbd className="ask-user-option-key">{i + 1}</kbd>
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
              <kbd className="ask-user-option-key">{question.options.length + 1}</kbd>
            </button>
            {draft.other ? (
              <textarea
                className="ask-user-text"
                rows={2}
                placeholder="Type your own answer here"
                value={draft.text}
                autoFocus
                onChange={(e) => setDraft({ text: e.target.value, other: true, selected: [] })}
                onKeyDown={(e) => {
                  if (
                    e.key === "Enter" &&
                    !e.shiftKey &&
                    !e.nativeEvent.isComposing &&
                    canSubmitQuestion(question, {
                      ...draft,
                      other: true,
                      text: (e.target as HTMLTextAreaElement).value,
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
          <textarea
            className="ask-user-text ask-user-text--solo"
            rows={2}
            placeholder="Type your answer here"
            value={draft.text}
            autoFocus
            onChange={(e) => setDraft({ text: e.target.value })}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing && canSubmit) {
                e.preventDefault();
                handleSubmit();
              }
            }}
          />
        ) : null}
      </div>
      <div className="ask-user-footer">
        <div className="ask-user-footer-left">
          {total > 1 ? (
            <button type="button" className="settings-btn ask-user-back" onClick={handleBack} disabled={index === 0}>
              <Icons.ChevronLeft />
              Back
            </button>
          ) : null}
          {canAttach ? (
            <>
              <button
                type="button"
                className="settings-btn ask-user-attach-btn"
                onClick={() => filePickerRef.current?.click()}
                disabled={attaching}
                title="Attach a file to your answer. Ducky saves it in this chat and tells the agent where it is."
              >
                <Icons.Paperclip />
                {attaching ? "Saving…" : "Attach"}
              </button>
              <input
                ref={filePickerRef}
                type="file"
                multiple
                hidden
                onChange={(e) => {
                  void attachFiles(e.target.files);
                  e.target.value = "";
                }}
              />
              {(draft.files ?? []).map((f) => (
                <span key={f.path} className="ask-user-attach-file" title={f.path}>
                  <Icons.Paperclip />
                  {f.name}
                  <button type="button" aria-label={`Remove ${f.name}`} onClick={() => removeFile(f.path)}>
                    <Icons.Close />
                  </button>
                </span>
              ))}
              {attachError ? (
                <span className="ask-user-attach-error" role="alert">
                  {attachError}
                </span>
              ) : null}
            </>
          ) : null}
        </div>
        <div className="ask-user-footer-right">
          <button
            type="button"
            className="settings-btn ask-user-cancel"
            onClick={handleCancel}
            title="Don't answer: close this question and tell the agent to stop"
          >
            Cancel
          </button>
          {canSkip ? (
            <button type="button" className="settings-btn" onClick={handleSkip}>
              Skip
            </button>
          ) : null}
          <button
            type="button"
            className={`settings-btn modal-confirm-btn ask-user-submit${canSubmit ? "" : " is-disabled"}`}
            onClick={handleSubmit}
            disabled={!canSubmit}
          >
            {isLast ? "Submit" : "Next"}
            <kbd className="ask-user-submit-key" aria-label="Enter">
              <Icons.Enter />
            </kbd>
          </button>
        </div>
      </div>
    </div>
  );
});
