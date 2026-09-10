import type { ReactNode } from "react";
import { Icons } from "../../icons/Icons";
import type { RichInventoryItem, RichInventoryKind } from "../../types/richContent";
import { inventoryKindFromLabel } from "./inventoryKind";
import { RichHeading } from "./RichHeading";

interface RichInventoryProps {
  items: RichInventoryItem[];
  folder?: string;
  heading?: string;
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

export function RichInventory({ items, folder, heading }: RichInventoryProps) {
  if (!items.length) return null;
  const title = heading?.trim() || "Inventory Added";
  return (
    <div className="rich-inventory">
      <div className="rich-inventory-head">
        <RichHeading level={2}>{title}</RichHeading>
        {folder ? <code className="rich-code--inline rich-inventory-folder">{folder}</code> : null}
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
                  <code className="rich-code--inline rich-inventory-title">{item.title}</code>
                </div>
                {item.desc ? <p className="rich-inventory-desc">{item.desc}</p> : null}
              </div>
            </li>
          );
        })}
      </ul>
    </div>
  );
}
