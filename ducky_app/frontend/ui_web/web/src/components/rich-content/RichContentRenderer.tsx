import { startTransition, useEffect, useState } from "react";
import type { OpenFileHandler, ParsedRichContent } from "../../types/richContent";
import { getApi } from "../../hooks/usePanelApi";
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
  // Parsing and tokenizing the entire growing response on every stream update
  // makes long Cursor turns increasingly expensive. Keep the live tail as one
  // text node; parse rich blocks/Markdown once the response is complete — and
  // defer that parse off the critical frame via startTransition so evaluate_js
  // is not blocked by a multi-second ReactMarkdown longtask.
  const [parsed, setParsed] = useState<ParsedRichContent | null>(null);

  useEffect(() => {
    if (mode !== "full") {
      setParsed(null);
      return;
    }
    let cancelled = false;
    startTransition(() => {
      if (cancelled) return;
      // #region agent log
      const _t0 = performance.now();
      const _p = parseRichContent(text);
      const _dt = performance.now() - _t0;
      if (_dt > 200) {
        getApi()?.report_ui_perf([{ kind: "dbg_render", name: "markdown_parse", duration_ms: Math.round(_dt), text_len: text.length }]);
      }
      setParsed(_p);
      // #endregion
    });
    return () => {
      cancelled = true;
    };
  }, [mode, text]);

  if (!text.trim()) return null;

  if (mode === "streaming") {
    return <div className="rich-content rich-content--streaming">{text}</div>;
  }

  // While the deferred parse is pending, show plain text so the bubble paints
  // immediately after streaming ends.
  if (!parsed) {
    return <div className="rich-content rich-content--streaming">{text}</div>;
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
