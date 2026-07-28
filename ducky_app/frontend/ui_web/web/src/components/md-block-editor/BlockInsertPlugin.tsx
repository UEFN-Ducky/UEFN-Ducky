import { useLexicalComposerContext } from "@lexical/react/LexicalComposerContext";
import { $createCodeNode } from "@lexical/code";
import { INSERT_ORDERED_LIST_COMMAND, INSERT_UNORDERED_LIST_COMMAND } from "@lexical/list";
import { $createHeadingNode, $createQuoteNode, type HeadingTagType } from "@lexical/rich-text";
import { $setBlocksType } from "@lexical/selection";
import {
  $createParagraphNode,
  $createTextNode,
  $getRoot,
  $getSelection,
  $isRangeSelection,
  COMMAND_PRIORITY_LOW,
  KEY_DOWN_COMMAND,
  type LexicalEditor,
} from "lexical";
import { useCallback, useEffect, useState } from "react";
import { createPortal } from "react-dom";

export type BlockKind =
  | "paragraph"
  | "h1"
  | "h2"
  | "h3"
  | "bullet"
  | "number"
  | "quote"
  | "code"
  | "divider";

const BLOCK_ITEMS: { kind: BlockKind; label: string; hint: string }[] = [
  { kind: "paragraph", label: "Paragraph", hint: "p" },
  { kind: "h1", label: "Heading 1", hint: "h1" },
  { kind: "h2", label: "Heading 2", hint: "h2" },
  { kind: "h3", label: "Heading 3", hint: "h3" },
  { kind: "bullet", label: "Bullet list", hint: "ul" },
  { kind: "number", label: "Numbered list", hint: "ol" },
  { kind: "quote", label: "Quote", hint: ">" },
  { kind: "code", label: "Code block", hint: "```" },
  { kind: "divider", label: "Divider", hint: "---" },
];

function ensureRangeSelection(): boolean {
  const selection = $getSelection();
  if ($isRangeSelection(selection)) return true;
  $getRoot().selectEnd();
  return $isRangeSelection($getSelection());
}

function insertBlock(editor: LexicalEditor, kind: BlockKind): void {
  if (kind === "bullet") {
    editor.update(() => {
      ensureRangeSelection();
    });
    editor.dispatchCommand(INSERT_UNORDERED_LIST_COMMAND, undefined);
    return;
  }
  if (kind === "number") {
    editor.update(() => {
      ensureRangeSelection();
    });
    editor.dispatchCommand(INSERT_ORDERED_LIST_COMMAND, undefined);
    return;
  }
  editor.update(() => {
    if (!ensureRangeSelection()) return;
    const selection = $getSelection();
    if (!$isRangeSelection(selection)) return;
    if (kind === "code") {
      $setBlocksType(selection, () => $createCodeNode());
      return;
    }
    if (kind === "quote") {
      $setBlocksType(selection, () => $createQuoteNode());
      return;
    }
    if (kind === "divider") {
      const para = $createParagraphNode();
      para.append($createTextNode("---"));
      const after = $createParagraphNode();
      selection.insertNodes([para, after]);
      after.select();
      return;
    }
    if (kind === "paragraph") {
      $setBlocksType(selection, () => $createParagraphNode());
      return;
    }
    const tag = kind as HeadingTagType;
    $setBlocksType(selection, () => $createHeadingNode(tag));
  });
}

export function BlockInsertPlugin({ disabled }: { disabled?: boolean }) {
  const [editor] = useLexicalComposerContext();
  const [menu, setMenu] = useState<{ x: number; y: number; slash?: boolean } | null>(null);

  const close = useCallback(() => setMenu(null), []);

  const pick = useCallback(
    (kind: BlockKind) => {
      insertBlock(editor, kind);
      close();
      // Focus after menu mousedown prevented blur
      queueMicrotask(() => editor.focus());
    },
    [editor, close],
  );

  useEffect(() => {
    if (disabled) return;
    return editor.registerCommand(
      KEY_DOWN_COMMAND,
      (event) => {
        if (event.key === "/" && !event.ctrlKey && !event.metaKey && !event.altKey) {
          const sel = window.getSelection();
          if (!sel || sel.rangeCount === 0) return false;
          const rect = sel.getRangeAt(0).getBoundingClientRect();
          setTimeout(() => {
            setMenu({
              x: rect.left || 80,
              y: (rect.bottom || 80) + 4,
              slash: true,
            });
          }, 0);
        }
        if (event.key === "Escape" && menu) {
          close();
          return true;
        }
        return false;
      },
      COMMAND_PRIORITY_LOW,
    );
  }, [editor, disabled, menu, close]);

  useEffect(() => {
    if (!menu) return;
    const onDoc = (e: MouseEvent) => {
      const t = e.target as Element | null;
      if (t?.closest(".md-block-menu")) return;
      close();
    };
    document.addEventListener("mousedown", onDoc);
    return () => document.removeEventListener("mousedown", onDoc);
  }, [menu, close]);

  return (
    <>
      {!disabled ? (
        <button
          type="button"
          className="md-block-plus"
          title="Add block"
          aria-label="Add block"
          onMouseDown={(e) => e.preventDefault()}
          onClick={(e) => {
            const r = e.currentTarget.getBoundingClientRect();
            setMenu({ x: r.left, y: r.bottom + 4 });
            editor.focus();
          }}
        >
          +
        </button>
      ) : null}
      {menu
        ? createPortal(
            <div
              className="md-block-menu"
              role="menu"
              ref={(el) => {
                if (!el) return;
                el.style.setProperty("left", `${menu.x}px`);
                el.style.setProperty("top", `${menu.y}px`);
              }}
            >
              {BLOCK_ITEMS.map((item) => (
                <button
                  key={item.kind}
                  type="button"
                  role="menuitem"
                  className="md-block-menu-item"
                  onMouseDown={(e) => e.preventDefault()}
                  onClick={() => pick(item.kind)}
                >
                  <span>{item.label}</span>
                  <span className="md-block-menu-hint">{item.hint}</span>
                </button>
              ))}
            </div>,
            document.body,
          )
        : null}
    </>
  );
}
