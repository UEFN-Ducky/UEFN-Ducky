import type { OpenFileHandler } from "../../types/richContent";
import { highlightRichCode } from "./highlightRichCode";
import { RichCodeChip } from "./RichCodeChip";

interface RichCodeBlockProps {
  text: string;
  language?: string;
  inline?: boolean;
  onOpenFile?: OpenFileHandler;
}

export function RichCodeBlock({ text, language, inline, onOpenFile }: RichCodeBlockProps) {
  if (inline) {
    return <RichCodeChip text={text} onOpenFile={onOpenFile} />;
  }
  return (
    <pre className="rich-code rich-code--block">
      <code className={language ? `language-${language}` : undefined}>
        {highlightRichCode(text).map((span, i) =>
          span.kind ? (
            <span key={i} className={`rich-code-tok rich-code-tok--${span.kind}`}>{span.text}</span>
          ) : (
            span.text
          ),
        )}
      </code>
    </pre>
  );
}
