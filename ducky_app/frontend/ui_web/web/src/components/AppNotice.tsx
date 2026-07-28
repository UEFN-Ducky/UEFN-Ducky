import { Icons } from "../icons/Icons";

interface AppNoticeProps {
  message: string;
  className?: string;
}

function isErrorMessage(message: string): boolean {
  const lower = message.toLowerCase();
  return lower.includes("fail") || lower.includes("error") || lower.includes("cancelled");
}

/** Floating status toast — same pattern as appearance save notices. */
export function AppNotice({ message, className = "" }: AppNoticeProps) {
  if (!message) return null;
  const isError = isErrorMessage(message);
  return (
    <div
      className={`app-notice${isError ? " is-error" : ""}${className ? ` ${className}` : ""}`}
      role="status"
      aria-live="polite"
    >
      {isError ? <Icons.ErrorCircle /> : <Icons.Check />}
      <span>{message}</span>
    </div>
  );
}
