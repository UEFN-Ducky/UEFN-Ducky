import type { RichBlock, OpenFileHandler } from "../../types/richContent";
import { RichAccordion } from "./RichAccordion";
import { RichCallout } from "./RichCallout";
import { RichCodeBlock } from "./RichCodeBlock";
import { RichFileLink } from "./RichFileLink";
import { RichHeading } from "./RichHeading";
import { RichInventory } from "./RichInventory";
import { RichKeyValue } from "./RichKeyValue";
import { RichList } from "./RichList";
import { RichParagraph } from "./RichParagraph";
import { RichReportHeader } from "./RichReportHeader";
import { RichStats } from "./RichStats";
import { RichTable } from "./RichTable";
import { RichInline } from "./RichInline";

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

export function RichBlockView({ block, onOpenFile, collapsePath }: RichBlockViewProps) {
  switch (block.type) {
    case "heading":
      return <RichHeading level={block.level} text={block.text}><RichInline text={block.text} onOpenFile={onOpenFile} /></RichHeading>;
    case "paragraph":
      return <RichParagraph><RichInline text={block.text} onOpenFile={onOpenFile} /></RichParagraph>;
    case "list":
      return <RichList ordered={block.ordered} items={block.items} onOpenFile={onOpenFile} />;
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
      return <RichTable headers={block.headers} rows={block.rows} onOpenFile={onOpenFile} />;
    case "key_value":
      return <RichKeyValue pairs={block.pairs} />;
    case "file_link":
      return <RichFileLink path={block.path} label={block.label} onOpenFile={onOpenFile} />;
    case "callout":
      return <RichCallout tone={block.tone} text={block.text} title={block.title} onOpenFile={onOpenFile} />;
    case "header":
      return <RichReportHeader title={block.title} command={block.command} onOpenFile={onOpenFile} />;
    case "stats":
      return (
        <RichStats changes={block.changes} blocked={block.blocked} programs={block.programs} />
      );
    case "inventory":
      return (
        <RichInventory items={block.items} folder={block.folder} heading={block.heading} onOpenFile={onOpenFile} />
      );
    default:
      return null;
  }
}
