import { chatCollapseKey, useChatCollapseScope, useChatCollapseState } from "../../hooks/useChatCollapseState";
import { Icons } from "../../icons/Icons";
import type { RichBlock } from "../../types/richContent";
import { RichBlockList } from "./RichBlockList";
import type { OpenFileHandler } from "../../types/richContent";

interface RichAccordionProps {
  title: string;
  blocks: RichBlock[];
  onOpenFile?: OpenFileHandler;
  defaultOpen?: boolean;
  collapsePath: string;
}

export function RichAccordion({ title, blocks, onOpenFile, defaultOpen = false, collapsePath }: RichAccordionProps) {
  const collapseScope = useChatCollapseScope();
  const [open, setOpen] = useChatCollapseState(
    chatCollapseKey(collapseScope, collapsePath, "accordion", title),
    defaultOpen,
  );

  return (
    <div className="rich-accordion">
      <button
        type="button"
        className={`rich-accordion-toggle${open ? " rich-accordion-toggle--expanded" : ""}`}
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
      >
        <span className="rich-accordion-title">{title}</span>
        <span className={`rich-accordion-chevron${open ? " rich-accordion-chevron--expanded" : ""}`}>
          <Icons.ChevronDown />
        </span>
      </button>
      <div className={`tool-card-collapse${open ? " is-open" : ""}`}>
        <div className="tool-card-collapse-inner">
          <div className="rich-accordion-body">
            <RichBlockList blocks={blocks} onOpenFile={onOpenFile} collapsePath={`${collapsePath}accordion:${title}/`} />
          </div>
        </div>
      </div>
    </div>
  );
}
