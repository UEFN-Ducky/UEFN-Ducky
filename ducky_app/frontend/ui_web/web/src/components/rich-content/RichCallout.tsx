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
