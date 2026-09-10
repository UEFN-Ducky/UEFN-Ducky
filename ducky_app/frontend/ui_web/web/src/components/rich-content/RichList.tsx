import type { OpenFileHandler } from "../../types/richContent";
import { RichInline } from "./RichInline";
import { richTextClass } from "./richTextColors";

interface RichListProps {
  ordered?: boolean;
  items: string[];
  onOpenFile?: OpenFileHandler;
}

export function RichList({ ordered, items, onOpenFile }: RichListProps) {
  const Tag = ordered ? "ol" : "ul";
  return (
    <Tag className={`rich-list${ordered ? " rich-list--ordered" : ""}`}>
      {items.map((item, i) => (
        <li key={`${i}-${item.slice(0, 24)}`} className={`rich-list-item ${richTextClass(item.replace(/[*`]/g, ""))}`}>
          <RichInline text={item} onOpenFile={onOpenFile} />
        </li>
      ))}
    </Tag>
  );
}
