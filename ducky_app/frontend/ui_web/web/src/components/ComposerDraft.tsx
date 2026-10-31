import { useEffect, useLayoutEffect, useRef, type CSSProperties, type KeyboardEvent, type Ref } from "react";
import { BUNDLED_DUCKIES } from "../generated/bundledDuckies";
import { requestOpenChatReference } from "../navigation/openChatReference";
import {
  chatRefDisplayName,
  chipTouchingCaret,
  expandRangeToChips,
  findChatRefTokens,
  parseChatRefHref,
} from "./chatReferences";
import { chatRefFace, useChatRefFaceVersion } from "./chatRefFaces";

function isImageIcon(value: string): boolean {
  return value.startsWith("data:image/") || /^https?:\/\//i.test(value) || /\.(png|jpe?g|svg|webp|gif)(\?|$)/i.test(value);
}

function readComposer(root: HTMLElement): string {
  let out = "";
  const walk = (node: Node, leadingBreak: boolean) => {
    if (node.nodeType === Node.TEXT_NODE) {
      out += node.textContent || "";
      return;
    }
    if (!(node instanceof HTMLElement)) return;
    if (node.dataset.md) {
      out += node.dataset.md;
      return;
    }
    if (node.tagName === "BR") {
      out += "\n";
      return;
    }
    if (leadingBreak && (node.tagName === "DIV" || node.tagName === "P")) out += "\n";
    node.childNodes.forEach((child, index) => walk(child, index > 0 && child instanceof HTMLElement && (child.tagName === "DIV" || child.tagName === "P")));
  };
  root.childNodes.forEach((child, index) => walk(child, index > 0));
  return out.replace(/\u00a0/g, " ");
}

function nodeLength(node: Node): number {
  if (node.nodeType === Node.TEXT_NODE) return node.textContent?.length ?? 0;
  if (!(node instanceof HTMLElement)) return 0;
  if (node.dataset.md) return node.dataset.md.length;
  if (node.tagName === "BR") return 1;
  let total = 0;
  node.childNodes.forEach((child) => {
    total += nodeLength(child);
  });
  return total;
}

function offsetAt(root: HTMLElement, container: Node, offset: number): number {
  let count = 0;
  const walk = (node: Node): boolean => {
    if (node === container) {
      if (node.nodeType === Node.TEXT_NODE) count += offset;
      else {
        Array.from(node.childNodes)
          .slice(0, offset)
          .forEach((kid) => {
            count += nodeLength(kid);
          });
      }
      return true;
    }
    if (node.nodeType === Node.TEXT_NODE) {
      count += node.textContent?.length ?? 0;
      return false;
    }
    if (node instanceof HTMLElement && node.dataset.md) {
      count += node.dataset.md.length;
      return false;
    }
    if (node instanceof HTMLElement && node.tagName === "BR") {
      count += 1;
      return false;
    }
    for (const child of Array.from(node.childNodes)) {
      if (walk(child)) return true;
    }
    return false;
  };
  return walk(root) ? count : readComposer(root).length;
}

export function composerCaret(root: HTMLElement | null): number {
  if (!root) return 0;
  const sel = window.getSelection();
  if (!sel || sel.rangeCount === 0) return readComposer(root).length;
  const range = sel.getRangeAt(0);
  if (!root.contains(range.startContainer)) return readComposer(root).length;
  return offsetAt(root, range.startContainer, range.startOffset);
}

function composerRange(root: HTMLElement): { start: number; end: number } {
  const sel = window.getSelection();
  const len = readComposer(root).length;
  if (!sel || sel.rangeCount === 0) return { start: len, end: len };
  const range = sel.getRangeAt(0);
  if (!root.contains(range.startContainer) || !root.contains(range.endContainer)) return { start: len, end: len };
  const start = offsetAt(root, range.startContainer, range.startOffset);
  const end = offsetAt(root, range.endContainer, range.endOffset);
  return start <= end ? { start, end } : { start: end, end: start };
}

function pointAt(root: HTMLElement, pos: number): { node: Node; offset: number } {
  let left = pos;
  const walk = (parent: Node): { node: Node; offset: number } | null => {
    const kids = Array.from(parent.childNodes);
    for (let i = 0; i < kids.length; i += 1) {
      const kid = kids[i];
      if (kid instanceof HTMLElement && kid.dataset.md) {
        const size = kid.dataset.md.length;
        if (left <= 0) return { node: parent, offset: i };
        if (left < size) return { node: parent, offset: i + 1 };
        left -= size;
        continue;
      }
      if (kid.nodeType === Node.TEXT_NODE) {
        const size = kid.textContent?.length ?? 0;
        if (left <= size) return { node: kid, offset: left };
        left -= size;
        continue;
      }
      if (kid instanceof HTMLElement && kid.tagName === "BR") {
        if (left <= 0) return { node: parent, offset: i };
        left -= 1;
        continue;
      }
      const nested = walk(kid);
      if (nested) return nested;
    }
    return null;
  };
  return walk(root) ?? { node: root, offset: root.childNodes.length };
}

export function composerSetCaret(root: HTMLElement | null, pos: number): void {
  if (!root) return;
  const point = pointAt(root, pos);
  const range = document.createRange();
  range.setStart(point.node, point.offset);
  range.collapse(true);
  const sel = window.getSelection();
  sel?.removeAllRanges();
  sel?.addRange(range);
}

function fillIcon(host: HTMLElement, href: string): void {
  const face = chatRefFace(href);
  const kind = parseChatRefHref(href)?.kind;
  host.replaceChildren();
  const styleUrl = face?.duckyStyle ? BUNDLED_DUCKIES.find((row) => row.id === face.duckyStyle)?.url : "";
  const icon = face?.iconUrl || "";
  if (styleUrl || (icon && isImageIcon(icon))) {
    const img = document.createElement("img");
    img.src = styleUrl || icon;
    img.alt = "";
    img.draggable = false;
    host.append(img);
    return;
  }
  if (icon) {
    host.textContent = icon;
    return;
  }
  host.textContent = kind === "mcp" ? "M" : kind === "plugin" ? "P" : kind === "file" ? "F" : kind === "profile" || kind === "ducky" ? "@" : "S";
}

function paintChip(tokenLabel: string, href: string, markdown: string): HTMLSpanElement {
  const chip = document.createElement("span");
  chip.className = "chat-ref-chip";
  chip.contentEditable = "false";
  chip.dataset.md = markdown;
  chip.dataset.href = href;
  const icon = document.createElement("span");
  icon.className = "chat-ref-chip-icon";
  fillIcon(icon, href);
  const name = document.createElement("span");
  name.className = "chat-ref-chip-label";
  name.textContent = chatRefDisplayName(tokenLabel);
  chip.title = name.textContent;
  chip.append(icon, name);
  let downX = 0;
  let downY = 0;
  chip.addEventListener("pointerdown", (event) => {
    downX = event.clientX;
    downY = event.clientY;
  });
  chip.addEventListener("click", (event) => {
    const dx = event.clientX - downX;
    const dy = event.clientY - downY;
    if (dx * dx + dy * dy > 16) return;
    event.preventDefault();
    event.stopPropagation();
    requestOpenChatReference(href, tokenLabel);
  });
  return chip;
}

function markSelectedChips(root: HTMLElement): void {
  const sel = window.getSelection();
  root.querySelectorAll<HTMLElement>(".chat-ref-chip.is-selected").forEach((chip) => {
    chip.classList.remove("is-selected");
  });
  if (!sel || sel.rangeCount === 0 || sel.isCollapsed) return;
  const range = sel.getRangeAt(0);
  const host = range.commonAncestorContainer;
  if (host !== root && !root.contains(host)) return;
  root.querySelectorAll<HTMLElement>(".chat-ref-chip").forEach((chip) => {
    if (range.intersectsNode(chip)) chip.classList.add("is-selected");
  });
}

function paint(root: HTMLElement, value: string): void {
  root.replaceChildren();
  if (!value) return;
  const tokens = findChatRefTokens(value);
  let cursor = 0;
  const pushText = (text: string) => {
    const parts = text.split("\n");
    parts.forEach((part, index) => {
      if (index) root.append(document.createElement("br"));
      if (part) root.append(document.createTextNode(part));
    });
  };
  for (const token of tokens) {
    if (token.start > cursor) pushText(value.slice(cursor, token.start));
    root.append(paintChip(token.label, token.href, value.slice(token.start, token.end)));
    cursor = token.end;
  }
  if (cursor < value.length) pushText(value.slice(cursor));
}

function refillIcons(root: HTMLElement): void {
  root.querySelectorAll<HTMLElement>(".chat-ref-chip-icon").forEach((icon) => {
    const href = icon.parentElement?.dataset.href || "";
    if (href) fillIcon(icon, href);
  });
}

interface ComposerDraftProps {
  value: string;
  caret: number;
  placeholder: string;
  className?: string;
  style?: CSSProperties;
  inputRef?: Ref<HTMLDivElement>;
  onChange: (value: string, caret: number) => void;
  onCaret: (caret: number) => void;
  onKeyDown?: (event: KeyboardEvent<HTMLDivElement>) => void;
  onFocus?: () => void;
  onBlur?: () => void;
}

/** A repaint (a paste, Shift+Enter) replaces the box's content and keeps its scroll where it
 *  was, so a long paste left the caret below the visible lines. Bring the caret back on
 *  screen; a caret with no box of its own (the end of a text node) at the very end of the
 *  text scrolls to the bottom. */
export function scrollCaretIntoView(root: HTMLElement, atEnd: boolean): void {
  if (root.scrollHeight <= root.clientHeight) return;
  const sel = window.getSelection();
  const rect = sel && sel.rangeCount ? sel.getRangeAt(0).getClientRects?.()[0] : undefined;
  if (!rect || (!rect.top && !rect.bottom)) {
    if (atEnd) root.scrollTop = root.scrollHeight;
    return;
  }
  const box = root.getBoundingClientRect();
  if (rect.bottom > box.bottom) root.scrollTop += rect.bottom - box.bottom;
  else if (rect.top < box.top) root.scrollTop -= box.top - rect.top;
}

/** Phones: Return starts a new line. Send stays on the button. Desktop Enter still sends. */
export function enterInsertsNewline(): boolean {
  return typeof window !== "undefined" && window.matchMedia("(pointer: coarse)").matches;
}

/** The composer text. Stored mentions stay markdown; the box shows icon chips. */
export function ComposerDraft({
  value,
  caret,
  placeholder,
  className,
  style,
  inputRef,
  onChange,
  onCaret,
  onKeyDown,
  onFocus,
  onBlur,
}: ComposerDraftProps) {
  const ref = useRef<HTMLDivElement | null>(null);
  const painted = useRef<string | null>(null);
  const faceVersion = useChatRefFaceVersion();

  useLayoutEffect(() => {
    const root = ref.current;
    if (!root) return;
    if (painted.current === value && root.childNodes.length > 0) return;
    if (painted.current === value && !value) return;
    const focused = document.activeElement === root;
    paint(root, value);
    painted.current = value;
    if (focused) {
      composerSetCaret(root, caret);
      scrollCaretIntoView(root, caret >= value.length);
    }
  }, [value, caret]);

  useLayoutEffect(() => {
    if (ref.current) refillIcons(ref.current);
  }, [faceVersion]);

  useEffect(() => {
    const mark = () => {
      if (ref.current) markSelectedChips(ref.current);
    };
    document.addEventListener("selectionchange", mark);
    return () => document.removeEventListener("selectionchange", mark);
  }, []);

  const setRefs = (node: HTMLDivElement | null) => {
    ref.current = node;
    if (typeof inputRef === "function") inputRef(node);
  };

  return (
    <div
      ref={setRefs}
      role="textbox"
      aria-multiline="true"
      contentEditable
      suppressContentEditableWarning
      data-placeholder={placeholder}
      className={`${className || ""}${value ? "" : " is-empty"}`}
      style={style}
      onInput={() => {
        const root = ref.current;
        if (!root) return;
        const next = readComposer(root);
        const messy = Boolean(root.querySelector("div, p"));
        painted.current = messy || !next ? null : next;
        onChange(next, composerCaret(root));
      }}
      onKeyUp={() => onCaret(composerCaret(ref.current))}
      onClick={() => onCaret(composerCaret(ref.current))}
      onPaste={(event) => {
        event.preventDefault();
        // Windows copies line ends as \r\n; the box keeps \n only.
        const text = event.clipboardData.getData("text/plain").replace(/\r\n?/g, "\n");
        if (!text || !ref.current) return;
        const current = readComposer(ref.current);
        const range = composerRange(ref.current);
        // A paste replaces what is selected (a mention chip it cuts into goes whole).
        const span = range.start !== range.end ? expandRangeToChips(current, range.start, range.end) : range;
        const next = current.slice(0, span.start) + text + current.slice(span.end);
        painted.current = null;
        onChange(next, span.start + text.length);
      }}
      onKeyDown={(event) => {
        if (event.key === "Backspace" || event.key === "Delete") {
          const root = ref.current;
          if (root) {
            const current = readComposer(root);
            const range = composerRange(root);
            const selected = range.start !== range.end ? expandRangeToChips(current, range.start, range.end) : null;
            const beside =
              selected || event.altKey || event.metaKey || event.ctrlKey
                ? null
                : chipTouchingCaret(current, range.start, event.key === "Backspace" ? "before" : "after");
            const cut = selected && findChatRefTokens(current).some((token) => token.start < selected.end && token.end > selected.start)
              ? selected
              : beside
                ? { start: beside.start, end: beside.end }
                : null;
            if (cut && cut.start !== cut.end) {
              event.preventDefault();
              painted.current = null;
              onChange(current.slice(0, cut.start) + current.slice(cut.end), cut.start);
              return;
            }
          }
        }
        const newline = () => {
          const root = ref.current;
          if (!root) return;
          const pos = composerCaret(root);
          const current = readComposer(root);
          painted.current = null;
          onChange(`${current.slice(0, pos)}\n${current.slice(pos)}`, pos + 1);
        };
        if (event.key === "Enter" && event.shiftKey) {
          event.preventDefault();
          newline();
          return;
        }
        onKeyDown?.(event);
        if (event.key === "Enter" && !event.shiftKey && !event.defaultPrevented && enterInsertsNewline()) {
          event.preventDefault();
          newline();
        }
      }}
      onFocus={onFocus}
      onBlur={onBlur}
    />
  );
}
