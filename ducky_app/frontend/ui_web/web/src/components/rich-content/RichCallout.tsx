import { Icons } from "../../icons/Icons";
import type { RichCalloutTone } from "../../types/richContent";

interface RichCalloutProps {
  tone: RichCalloutTone;
  text: string;
  title?: string;
}

function CalloutIcon({ tone }: { tone: RichCalloutTone }) {
  if (tone === "success") return <Icons.Check />;
  if (tone === "error") return <Icons.ErrorCircle />;
  return <Icons.AlertTriangle />;
}

export function RichCallout({ tone, text, title }: RichCalloutProps) {
  return (
    <div className={`rich-callout rich-callout--${tone}`}>
      {title ? (
        <div className="rich-callout-title">
          <CalloutIcon tone={tone} />
          {title}
        </div>
      ) : null}
      <div className="rich-callout-body">{text}</div>
    </div>
  );
}
