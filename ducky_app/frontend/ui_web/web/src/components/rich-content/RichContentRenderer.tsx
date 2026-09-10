import { startTransition, useEffect, useState } from "react";
import type { OpenFileHandler, ParsedRichContent } from "../../types/richContent";
import { parseRichContent } from "./parseRichContent";
import { MarkdownContent } from "./MarkdownContent";
import { RichBlockList } from "./RichBlockList";

export type RichContentMode = "full" | "streaming";

interface RichContentRendererProps {
  text: string;
  onOpenFile?: OpenFileHandler;
  mode?: RichContentMode;
}

export function RichContentRenderer({
  text,
  onOpenFile,
  mode = "full",
}: RichContentRendererProps) {
  // JSON __rich is parsed after the turn finishes. Markdown promotes live so
  // Run Summary / Inventory appear while the reply is still writing.
  const [parsed, setParsed] = useState<ParsedRichContent | null>(null);

  useEffect(() => {
    if (mode !== "full") {
      setParsed(null);
      return;
    }
    let cancelled = false;
    startTransition(() => {
      if (cancelled) return;
      setParsed(parseRichContent(text));
    });
    return () => {
      cancelled = true;
    };
  }, [mode, text]);

  if (!text.trim()) return null;

  // Live + pending-parse: render Markdown/widgets as sections complete so the
  // bubble is never a raw ## / ** dump. JSON __rich blocks swap in when parsed.
  if (mode === "streaming" || !parsed) {
    return (
      <div className="rich-content">
        <MarkdownContent text={text} onOpenFile={onOpenFile} />
      </div>
    );
  }

  if (parsed.kind === "blocks") {
    return (
      <div className="rich-content">
        {parsed.summary ? <div className="rich-summary">{parsed.summary}</div> : null}
        <RichBlockList blocks={parsed.blocks} onOpenFile={onOpenFile} />
      </div>
    );
  }

  return (
    <div className="rich-content">
      <MarkdownContent text={parsed.text ?? text} onOpenFile={onOpenFile} />
    </div>
  );
}
