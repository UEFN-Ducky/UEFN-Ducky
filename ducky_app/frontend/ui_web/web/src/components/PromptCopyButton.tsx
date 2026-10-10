import { useEffect, useRef, useState } from "react";
import { Icons } from "../icons/Icons";
import { copyText } from "../utils/copyText";

/** One-click copy for a prompt bubble. CSS shows it while the pointer is over the prompt. */
export function PromptCopyButton({ text }: { text: string }) {
  const [copied, setCopied] = useState(false);
  const timer = useRef<number | undefined>(undefined);
  useEffect(() => () => window.clearTimeout(timer.current), []);

  const label = copied ? "Copied" : "Copy prompt";
  return (
    <button
      type="button"
      className={`message-bubble-user-copy-btn${copied ? " is-copied" : ""}`}
      title={label}
      aria-label={label}
      onClick={(event) => {
        event.stopPropagation();
        void copyText(text).then((ok) => {
          if (!ok) return;
          setCopied(true);
          window.clearTimeout(timer.current);
          timer.current = window.setTimeout(() => setCopied(false), 1500);
        });
      }}
      // Enter/Space here copies; it must not also expand or edit the prompt around it.
      onKeyDown={(event) => event.stopPropagation()}
    >
      {copied ? <Icons.Check /> : <Icons.Copy />}
    </button>
  );
}
