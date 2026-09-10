import type { ReactNode } from "react";
import { Icons } from "../../icons/Icons";
import type { OpenFileHandler, RichInventoryItem, RichInventoryKind } from "../../types/richContent";
import { inventoryKindFromLabel } from "./inventoryKind";
import { RichCodeChip } from "./RichCodeChip";
import { RichHeading } from "./RichHeading";
import { RichInline } from "./RichInline";

interface RichInventoryProps {
  items: RichInventoryItem[];
  folder?: string;
  heading?: string;
  onOpenFile?: OpenFileHandler;
}

const KIND_ICON: Record<RichInventoryKind, () => ReactNode> = {
  verse: () => <Icons.Verse />,
  devices: () => <Icons.Zap />,
  prop: () => <Icons.Box />,
  blueprint: () => <Icons.File />,
  blender: () => <Icons.Box />,
  umg: () => <Icons.Monitor />,
  default: () => <Icons.Box />,
};

export function RichInventory({ items, folder, heading, onOpenFile }: RichInventoryProps) {
  if (!items.length) return null;
  const raw = heading?.trim() || "";
  const title = !raw || /^inventory$/i.test(raw) ? "Inventory Added" : raw;
  return (
    <div className="rich-inventory">
      <div className="rich-inventory-head">
        <RichHeading level={2}>{title}</RichHeading>
        {folder ? <RichCodeChip text={folder} onOpenFile={onOpenFile} /> : null}
      </div>
      <ul className="rich-inventory-list">
        {items.map((item, i) => {
          const kind = inventoryKindFromLabel(item.kind || item.label || "");
          const Icon = KIND_ICON[kind];
          const label = item.label?.trim() || item.kind;
          return (
            <li key={`${item.title}-${i}`} className={`rich-inventory-item rich-inventory-item--${kind}`}>
              <div className="rich-inventory-icon">
                <Icon />
              </div>
              <div className="rich-inventory-body">
                <div className="rich-inventory-meta">
                  <span className="rich-inventory-kind">{label}</span>
                  <span className="rich-inventory-slash">/</span>
                  <RichCodeChip text={item.title} onOpenFile={onOpenFile} />
                </div>
                {item.desc ? <p className="rich-inventory-desc"><RichInline text={item.desc} onOpenFile={onOpenFile} /></p> : null}
              </div>
            </li>
          );
        })}
      </ul>
    </div>
  );
}
