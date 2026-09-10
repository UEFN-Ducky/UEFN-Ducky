import type { OpenFileHandler } from "../../types/richContent";
import { LiveCodePreview } from "../../verse-editor/components/LiveCodePreview";
import { monacoLanguageForFence } from "../../verse-editor/utils/isVerseFile";
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
    <LiveCodePreview
      className="rich-code rich-code--block"
      value={text}
      language={monacoLanguageForFence(language, text)}
    />
  );
}
