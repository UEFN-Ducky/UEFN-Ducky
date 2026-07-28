import type { RichBlock, OpenFileHandler } from "../../types/richContent";
import { RichAccordion } from "./RichAccordion";
import { RichCallout } from "./RichCallout";
import { RichCodeBlock } from "./RichCodeBlock";
import { RichFileLink } from "./RichFileLink";
import { RichHeading } from "./RichHeading";
import { RichKeyValue } from "./RichKeyValue";
import { RichList } from "./RichList";
import { RichParagraph } from "./RichParagraph";
import { RichTable } from "./RichTable";

interface RichBlockListProps {
  blocks: RichBlock[];
  onOpenFile?: OpenFileHandler;
  collapsePath?: string;
}

export function RichBlockList({ blocks, onOpenFile, collapsePath = "" }: RichBlockListProps) {
  return (
    <div className="rich-block-list">
      {blocks.map((block, i) => (
        <RichBlockView
          key={`${block.type}-${i}`}
          block={block}
          onOpenFile={onOpenFile}
          collapsePath={`${collapsePath}${i}:`}
        />
      ))}
    </div>
  );
}

interface RichBlockViewProps {
  block: RichBlock;
  onOpenFile?: OpenFileHandler;
  collapsePath: string;
}

function RichBlockView({ block, onOpenFile, collapsePath }: RichBlockViewProps) {
  switch (block.type) {
    case "heading":
      return (
        <RichHeading level={block.level}>
          {block.text}
        </RichHeading>
      );
    case "paragraph":
      return <RichParagraph>{block.text}</RichParagraph>;
    case "list":
      return <RichList ordered={block.ordered} items={block.items} />;
    case "code":
      return <RichCodeBlock text={block.text} language={block.language} />;
    case "accordion":
      return (
        <RichAccordion
          title={block.title}
          blocks={block.blocks}
          onOpenFile={onOpenFile}
          collapsePath={collapsePath}
        />
      );
    case "table":
      return <RichTable headers={block.headers} rows={block.rows} />;
    case "key_value":
      return <RichKeyValue pairs={block.pairs} />;
    case "file_link":
      return (
        <RichFileLink path={block.path} label={block.label} onOpenFile={onOpenFile} />
      );
    case "callout":
      return <RichCallout tone={block.tone} text={block.text} />;
    default:
      return null;
  }
}
