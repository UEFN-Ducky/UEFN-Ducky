import { Modal } from "../components/Modal";
import { AskUserForm } from "./AskUserForm";
import type { AskUserQuestion, AskUserResult } from "./types";

type Props = {
  open: boolean;
  questions: AskUserQuestion[];
  title: string;
  queueAhead: number;
  sessionId?: string;
  onComplete: (result: AskUserResult) => void;
};

/** Fallback when ask_user has no conv_id (external MCP with no chat context). */
export function AskUserModal({ open, questions, title, queueAhead, onComplete }: Props) {
  const headerTitle = title || "Clarify";

  return (
    <Modal
      open={open && questions.length > 0}
      onClose={() => {
        const answers: AskUserResult["answers"] = {};
        for (const q of questions) {
          answers[q.id] = { selected: [], text: "", skipped: true };
        }
        onComplete({
          ok: true,
          answers,
          skipped_all: true,
        });
      }}
      title={headerTitle}
      width={520}
      hideHeader
      floatingClose
      zIndex={100100}
    >
      {open && questions.length ? (
        <AskUserForm
          questions={questions}
          title={title}
          queueAhead={queueAhead}
          captureKeys
          showDismiss={false}
          onComplete={onComplete}
        />
      ) : null}
    </Modal>
  );
}
