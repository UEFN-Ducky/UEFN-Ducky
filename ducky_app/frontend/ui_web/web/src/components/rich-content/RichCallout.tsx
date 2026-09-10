import { Icons } from "../../icons/Icons";
import type { OpenFileHandler, RichCalloutTone } from "../../types/richContent";
import { RichInline } from "./RichInline";

interface RichCalloutProps {
  tone: RichCalloutTone;
  text: string;
  title?: string;
  onOpenFile?: OpenFileHandler;
}

function CalloutIcon({ tone }: { tone: RichCalloutTone }) {
  if (tone === "success") return <Icons.Check />;
  if (tone === "error") return <Icons.ErrorCircle />;
  if (tone === "info") return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden>
      <circle cx="12" cy="12" r="9" />
      <path d="M12 11v6M12 7h.01" />
    </svg>
  );
  return <Icons.AlertTriangle />;
}

export function RichCallout({ tone, text, title, onOpenFile }: RichCalloutProps) {
  const label = title || { info: "Note", warn: "Warning", error: "Error", success: "Verified" }[tone];
  return (
    <div className={`rich-callout rich-callout--${tone}`}>
      <div className="rich-callout-title">
        <CalloutIcon tone={tone} />
        {label}
      </div>
      <div className="rich-callout-body"><RichInline text={text} onOpenFile={onOpenFile} /></div>
    </div>
  );
}
