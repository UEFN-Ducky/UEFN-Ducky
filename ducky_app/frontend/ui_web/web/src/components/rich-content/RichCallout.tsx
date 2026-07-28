import type { RichCalloutTone } from "../../types/richContent";

interface RichCalloutProps {
  tone: RichCalloutTone;
  text: string;
}

export function RichCallout({ tone, text }: RichCalloutProps) {
  return <div className={`rich-callout rich-callout--${tone}`}>{text}</div>;
}
