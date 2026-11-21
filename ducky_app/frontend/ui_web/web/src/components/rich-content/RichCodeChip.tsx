import { useId, useState, type KeyboardEvent, type MouseEvent } from "react";
import { createPortal } from "react-dom";
import { getApi, isRemote } from "../../hooks/usePanelApi";
import type { OpenFileHandler } from "../../types/richContent";
import { basename } from "../../verse-editor/utils/isVerseFile";
import { classifyRichRef, type RichRefOpen } from "./classifyRichRef";

function openChip(open: RichRefOpen, onOpenFile?: OpenFileHandler): void {
  if (open.type === "local") {
    void getApi()?.open_path_in_explorer?.(open.path);
    return;
  }
  if (open.type === "file") {
    if (onOpenFile && open.line) onOpenFile(open.path, basename(open.path), { line: open.line });
    else if (onOpenFile) onOpenFile(open.path, basename(open.path));
    else void getApi()?.open_project_file(open.path);
    return;
  }
  void getApi()?.open_asset_in_uefn?.(open.path);
}

/** Enter or Space runs an inline role="button" span the way a real button would. */
export function onActivateKey(run: () => void) {
  return (event: KeyboardEvent<HTMLElement>) => {
    if (event.key !== "Enter" && event.key !== " ") return;
    event.preventDefault();
    run();
  };
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
          {ref.hint ? <div className="rich-ref-tip-hint">{ref.hint}</div> : null}
        </div>,
        document.body,
      )
    : null;

  const hover = {
    "aria-describedby": pos ? tipId : undefined,
    onMouseEnter: (e: MouseEvent<HTMLElement>) => show(e.currentTarget),
    onMouseLeave: () => setPos(null),
  };
  // Not on a phone: the Explorer window would open on the PC.
  const open = ref.open?.type === "local" && isRemote() ? undefined : ref.open;
  if (!open) {
    // Only a name, not a link: pressing it does nothing, so a click never copies or
    // selects-and-copies over what the person was doing.
    return (
      <>
        <span className={`rich-code rich-code--inline rich-ref rich-ref--${ref.kind} rich-ref--plain`} {...hover}>
          {text}
        </span>
        {tip}
      </>
    );
  }

  // A span, not a <button>: a button is always its own box, so a long name wrapped inside it
  // with centred lines on a phone. An inline span flows and wraps with the sentence.
  const run = () => openChip(open, onOpenFile);
  return (
    <>
      <span
        role="button"
        tabIndex={0}
        className={`rich-code rich-code--inline rich-ref rich-ref--${ref.kind} rich-ref--open`}
        aria-label={`${ref.label}: ${ref.text}. ${ref.hint}`}
        {...hover}
        onFocus={(e) => show(e.currentTarget)}
        onBlur={() => setPos(null)}
        onClick={run}
        onKeyDown={onActivateKey(run)}
      >
        {text}
      </span>
      {tip}
    </>
  );
}
