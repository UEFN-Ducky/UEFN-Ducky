import { useId, useState } from "react";
import { createPortal } from "react-dom";
import { getApi } from "../../hooks/usePanelApi";
import type { OpenFileHandler } from "../../types/richContent";
import { basename } from "../../verse-editor/utils/isVerseFile";
import { classifyRichRef } from "./classifyRichRef";
import { richTextClass } from "./richTextColors";

function openChip(text: string, onOpenFile?: OpenFileHandler): void {
  const ref = classifyRichRef(text);
  if (ref.open?.type === "file") {
    const path = ref.open.path;
    const name = basename(path);
    if (onOpenFile) onOpenFile(path, name);
    else void getApi()?.open_project_file(path);
    return;
  }
  if (ref.open?.type === "asset") {
    void getApi()?.open_asset_in_uefn?.(ref.open.path);
    return;
  }
  void navigator.clipboard?.writeText(ref.text);
}

export function RichCodeChip({ text, onOpenFile }: { text: string; onOpenFile?: OpenFileHandler }) {
  const ref = classifyRichRef(text);
  const tipId = useId();
  const [pos, setPos] = useState<{ x: number; y: number } | null>(null);

  const show = (el: HTMLElement) => {
    const box = el.getBoundingClientRect();
    setPos({ x: box.left, y: box.bottom + 6 });
  };

  const tip = pos && typeof document !== "undefined"
    ? createPortal(
        <div
          id={tipId}
          role="tooltip"
          className="rich-ref-tip"
          ref={(node) => {
            if (!node) return;
            node.style.setProperty("left", `${pos.x}px`);
            node.style.setProperty("top", `${pos.y}px`);
          }}
        >
          <div className="rich-ref-tip-kind">{ref.label}</div>
          <div className="rich-ref-tip-name">{ref.text}</div>
          <div className="rich-ref-tip-hint">{ref.hint}</div>
        </div>,
        document.body,
      )
    : null;

  return (
    <>
      <button
        type="button"
        className={`rich-code rich-code--inline rich-ref ${ref.open ? "rich-ref--open" : "rich-ref--copy"} ${richTextClass(text)}`}
        aria-describedby={pos ? tipId : undefined}
        aria-label={`${ref.label}: ${ref.text}. ${ref.hint}`}
        onMouseEnter={(e) => show(e.currentTarget)}
        onMouseLeave={() => setPos(null)}
        onFocus={(e) => show(e.currentTarget)}
        onBlur={() => setPos(null)}
        onClick={() => openChip(text, onOpenFile)}
      >
        {text}
      </button>
      {tip}
    </>
  );
}
