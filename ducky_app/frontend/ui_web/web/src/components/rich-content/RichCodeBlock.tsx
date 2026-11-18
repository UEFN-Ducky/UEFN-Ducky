import type { OpenFileHandler } from "../../types/richContent";
import { LiveCodePreview } from "../../verse-editor/components/LiveCodePreview";
import { monacoLanguageForFence } from "../../verse-editor/utils/isVerseFile";
import { RichCodeChip } from "./RichCodeChip";
import { useRichGraphic } from "./RichChart";

interface RichCodeBlockProps {
  text: string;
  language?: string;
  inline?: boolean;
  onOpenFile?: OpenFileHandler;
  /** The reply is still streaming: plain text, no highlighter or editor yet. */
  streaming?: boolean;
}

export function RichCodeBlock({ text, language, inline, onOpenFile, streaming }: RichCodeBlockProps) {
  const graphic = useRichGraphic(inline ? "" : text, inline ? undefined : language);
  if (inline) {
    return <RichCodeChip text={text} onOpenFile={onOpenFile} />;
  }
  if (graphic) return graphic;
  return (
    <LiveCodePreview
      className="rich-code rich-code--block"
      value={text}
      language={monacoLanguageForFence(language, text)}
      staticUntilClick
      plain={streaming}
    />
  );
}
