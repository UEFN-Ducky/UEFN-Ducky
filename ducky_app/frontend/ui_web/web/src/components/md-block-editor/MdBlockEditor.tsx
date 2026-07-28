import { CodeHighlightNode, CodeNode } from "@lexical/code";
import { $createLinkNode, LinkNode, TOGGLE_LINK_COMMAND } from "@lexical/link";
import { ListItemNode, ListNode } from "@lexical/list";
import { $convertFromMarkdownString, $convertToMarkdownString } from "@lexical/markdown";
import { LexicalComposer } from "@lexical/react/LexicalComposer";
import { useLexicalComposerContext } from "@lexical/react/LexicalComposerContext";
import { ContentEditable } from "@lexical/react/LexicalContentEditable";
import { LexicalErrorBoundary } from "@lexical/react/LexicalErrorBoundary";
import { HistoryPlugin } from "@lexical/react/LexicalHistoryPlugin";
import { LinkPlugin } from "@lexical/react/LexicalLinkPlugin";
import { ListPlugin } from "@lexical/react/LexicalListPlugin";
import { MarkdownShortcutPlugin } from "@lexical/react/LexicalMarkdownShortcutPlugin";
import { OnChangePlugin } from "@lexical/react/LexicalOnChangePlugin";
import { RichTextPlugin } from "@lexical/react/LexicalRichTextPlugin";
import { HeadingNode, QuoteNode } from "@lexical/rich-text";
import {
  $createParagraphNode,
  $createTextNode,
  $getRoot,
  $getSelection,
  $isRangeSelection,
  FORMAT_TEXT_COMMAND,
  type EditorState,
  type LexicalEditor,
} from "lexical";
import { useCallback, useEffect, useMemo, useRef } from "react";

import { BlockInsertPlugin } from "./BlockInsertPlugin";
import { MD_TRANSFORMERS } from "./markdown";
import "./md-block-editor.css";

export type PlanLinkOption = { id: string; label: string };

export interface MdBlockEditorProps {
  value: string;
  onChange: (markdown: string) => void;
  readOnly?: boolean;
  placeholder?: string;
  className?: string;
  /** Outline steps/subplans — toolbar dropdown links the current block for scroll/highlight. */
  planLinkOptions?: PlanLinkOption[];
}

function moveTopLevelBlock(direction: -1 | 1): void {
  const selection = $getSelection();
  if (!$isRangeSelection(selection)) {
    $getRoot().selectEnd();
  }
  const sel = $getSelection();
  if (!$isRangeSelection(sel)) return;
  const block = sel.anchor.getNode().getTopLevelElementOrThrow();
  const sibling = direction < 0 ? block.getPreviousSibling() : block.getNextSibling();
  if (!sibling) return;
  if (direction < 0) sibling.insertBefore(block);
  else sibling.insertAfter(block);
  block.selectEnd();
}

function linkBlockToPlanNode(nodeId: string): void {
  const id = (nodeId || "").trim();
  if (!id) return;
  let selection = $getSelection();
  if (!$isRangeSelection(selection)) {
    $getRoot().selectEnd();
    selection = $getSelection();
  }
  if (!$isRangeSelection(selection)) return;
  const block = selection.anchor.getNode().getTopLevelElementOrThrow();
  const marker = $createParagraphNode();
  const link = $createLinkNode(`plan-node:${id}`);
  link.append($createTextNode("§"));
  marker.append(link);
  block.insertBefore(marker);
}

function MarkdownSyncPlugin({
  value,
  onChange,
  readOnly,
}: {
  value: string;
  onChange: (md: string) => void;
  readOnly?: boolean;
}) {
  const [editor] = useLexicalComposerContext();
  const lastEmitted = useRef<string | null>(null);
  const skipNext = useRef(false);

  useEffect(() => {
    editor.setEditable(!readOnly);
  }, [editor, readOnly]);

  useEffect(() => {
    if (value === lastEmitted.current) return;
    skipNext.current = true;
    editor.update(() => {
      $convertFromMarkdownString(value || "", MD_TRANSFORMERS);
    });
    lastEmitted.current = value;
  }, [editor, value]);

  const handleChange = useCallback(
    (state: EditorState, _ed: LexicalEditor) => {
      if (skipNext.current) {
        skipNext.current = false;
        return;
      }
      if (readOnly) return;
      state.read(() => {
        const md = $convertToMarkdownString(MD_TRANSFORMERS);
        if (md === lastEmitted.current) return;
        lastEmitted.current = md;
        onChange(md);
      });
    },
    [onChange, readOnly],
  );

  return <OnChangePlugin onChange={handleChange} ignoreSelectionChange />;
}

function Toolbar({
  disabled,
  planLinkOptions,
}: {
  disabled?: boolean;
  planLinkOptions?: PlanLinkOption[];
}) {
  const [editor] = useLexicalComposerContext();
  if (disabled) return null;
  return (
    <div className="md-block-toolbar" role="toolbar" aria-label="Formatting">
      <button
        type="button"
        className="md-block-toolbar-btn"
        title="Bold"
        onMouseDown={(e) => e.preventDefault()}
        onClick={() => editor.dispatchCommand(FORMAT_TEXT_COMMAND, "bold")}
      >
        B
      </button>
      <button
        type="button"
        className="md-block-toolbar-btn md-block-toolbar-btn--italic"
        title="Italic"
        onMouseDown={(e) => e.preventDefault()}
        onClick={() => editor.dispatchCommand(FORMAT_TEXT_COMMAND, "italic")}
      >
        I
      </button>
      <button
        type="button"
        className="md-block-toolbar-btn"
        title="Inline code"
        onMouseDown={(e) => e.preventDefault()}
        onClick={() => editor.dispatchCommand(FORMAT_TEXT_COMMAND, "code")}
      >
        {"</>"}
      </button>
      <button
        type="button"
        className="md-block-toolbar-btn"
        title="Link"
        onMouseDown={(e) => e.preventDefault()}
        onClick={() => {
          const url = window.prompt("Link URL");
          if (!url?.trim()) return;
          editor.dispatchCommand(TOGGLE_LINK_COMMAND, url.trim());
        }}
      >
        Link
      </button>
      <button
        type="button"
        className="md-block-toolbar-btn"
        title="Move block up"
        aria-label="Move block up"
        onMouseDown={(e) => e.preventDefault()}
        onClick={() => editor.update(() => moveTopLevelBlock(-1))}
      >
        ↑
      </button>
      <button
        type="button"
        className="md-block-toolbar-btn"
        title="Move block down"
        aria-label="Move block down"
        onMouseDown={(e) => e.preventDefault()}
        onClick={() => editor.update(() => moveTopLevelBlock(1))}
      >
        ↓
      </button>
      {planLinkOptions && planLinkOptions.length > 0 ? (
        <label className="md-block-plan-link">
          <span className="md-block-plan-link-label">Step</span>
          <select
            className="md-block-plan-link-select"
            defaultValue=""
            aria-label="Link block to plan step"
            onMouseDown={(e) => e.stopPropagation()}
            onChange={(e) => {
              const id = e.target.value;
              e.target.value = "";
              if (!id) return;
              editor.update(() => linkBlockToPlanNode(id));
              queueMicrotask(() => editor.focus());
            }}
          >
            <option value="" disabled>
              Link block to step…
            </option>
            {planLinkOptions.map((opt) => (
              <option key={opt.id} value={opt.id}>
                {opt.label}
              </option>
            ))}
          </select>
        </label>
      ) : null}
    </div>
  );
}

const theme = {
  paragraph: "md-block-p",
  heading: {
    h1: "md-block-h1",
    h2: "md-block-h2",
    h3: "md-block-h3",
  },
  quote: "md-block-quote",
  list: {
    ul: "md-block-ul",
    ol: "md-block-ol",
    listitem: "md-block-li",
  },
  code: "md-block-code",
  text: {
    bold: "md-block-bold",
    italic: "md-block-italic",
    code: "md-block-inline-code",
  },
  link: "md-block-link",
};

export function MdBlockEditor({
  value,
  onChange,
  readOnly = false,
  placeholder = "Write markdown…",
  className = "",
  planLinkOptions,
}: MdBlockEditorProps) {
  const initialConfig = useMemo(
    () => ({
      namespace: "MdBlockEditor",
      theme,
      editable: !readOnly,
      onError: (err: Error) => {
        console.error("[MdBlockEditor]", err);
      },
      nodes: [
        HeadingNode,
        QuoteNode,
        ListNode,
        ListItemNode,
        CodeNode,
        CodeHighlightNode,
        LinkNode,
      ],
      editorState: () => {
        $convertFromMarkdownString(value || "", MD_TRANSFORMERS);
      },
    }),
    // eslint-disable-next-line react-hooks/exhaustive-deps -- mount-only initial MD
    [],
  );

  return (
    <div className={`md-block-editor${readOnly ? " is-readonly" : ""}${className ? ` ${className}` : ""}`}>
      <LexicalComposer initialConfig={initialConfig}>
        <Toolbar disabled={readOnly} planLinkOptions={planLinkOptions} />
        <div className="md-block-editor-shell">
          <BlockInsertPlugin disabled={readOnly} />
          <div className="md-block-editor-body">
            <RichTextPlugin
              contentEditable={<ContentEditable className="md-block-editable" aria-label="Markdown editor" />}
              placeholder={<div className="md-block-placeholder">{placeholder}</div>}
              ErrorBoundary={LexicalErrorBoundary}
            />
          </div>
        </div>
        <HistoryPlugin />
        <ListPlugin />
        <LinkPlugin
          validateUrl={(url) =>
            url.startsWith("plan-node:") ||
            url.startsWith("http://") ||
            url.startsWith("https://") ||
            url.startsWith("mailto:")
          }
        />
        <MarkdownShortcutPlugin transformers={MD_TRANSFORMERS} />
        <MarkdownSyncPlugin value={value} onChange={onChange} readOnly={readOnly} />
      </LexicalComposer>
    </div>
  );
}
