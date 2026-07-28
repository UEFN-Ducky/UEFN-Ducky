import type { OpenFileHandler } from "../../types/richContent";
import { autoFormatToolJson } from "../rich-content/autoFormatToolJson";
import { RichBlockList } from "../rich-content/RichBlockList";
import { resolveToolPresenterBlocks } from "./resolveToolPresenter";

interface ToolPresenterViewProps {
  toolName: string;
  arguments: Record<string, unknown>;
  resultText: string;
  isSuccess: boolean;
  onOpenFile?: OpenFileHandler;
}

export function ToolPresenterView({
  toolName,
  arguments: args,
  resultText,
  onOpenFile,
}: ToolPresenterViewProps) {
  const custom = resolveToolPresenterBlocks({
    toolName,
    arguments: args,
    resultText,
    isSuccess: true,
    onOpenFile,
  });
  const blocks = custom ?? autoFormatToolJson(resultText);
  if (blocks.length === 0) return null;
  return (
    <div className="tool-result-rich-view">
      <RichBlockList blocks={blocks} onOpenFile={onOpenFile} />
    </div>
  );
}
