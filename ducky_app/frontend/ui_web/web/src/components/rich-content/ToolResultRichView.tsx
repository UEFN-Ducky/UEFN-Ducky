import type { OpenFileHandler } from "../../types/richContent";
import { autoFormatToolJson } from "./autoFormatToolJson";
import { RichBlockList } from "./RichBlockList";

interface ToolResultRichViewProps {
  resultText: string;
  onOpenFile?: OpenFileHandler;
}

export function ToolResultRichView({ resultText, onOpenFile }: ToolResultRichViewProps) {
  const blocks = autoFormatToolJson(resultText);
  if (blocks.length === 0) return null;
  return (
    <div className="tool-result-rich-view">
      <RichBlockList blocks={blocks} onOpenFile={onOpenFile} />
    </div>
  );
}
