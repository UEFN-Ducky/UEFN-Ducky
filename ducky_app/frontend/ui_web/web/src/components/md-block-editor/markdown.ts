import { CodeNode } from "@lexical/code";
import { LinkNode } from "@lexical/link";
import { ListItemNode, ListNode } from "@lexical/list";
import {
  $convertFromMarkdownString,
  $convertToMarkdownString,
  TRANSFORMERS,
} from "@lexical/markdown";
import { HeadingNode, QuoteNode } from "@lexical/rich-text";
import { createEditor, type LexicalEditor } from "lexical";

/** Shared transformers for MD import/export (headings, lists, quote, code, link, …). */
export const MD_TRANSFORMERS = TRANSFORMERS;

const NODES = [HeadingNode, QuoteNode, ListNode, ListItemNode, CodeNode, LinkNode];

/** Build a throwaway Lexical editor for headless MD round-trips (tests / helpers). */
export function createMdEditor(): LexicalEditor {
  return createEditor({
    namespace: "MdBlockEditor",
    nodes: NODES,
    onError: (err) => {
      throw err;
    },
  });
}

/** Parse markdown into a Lexical editor (replaces current root). */
export function importMarkdown(editor: LexicalEditor, markdown: string): void {
  editor.update(
    () => {
      $convertFromMarkdownString(markdown || "", MD_TRANSFORMERS);
    },
    { discrete: true },
  );
}

/** Serialize current editor state to markdown. */
export function exportMarkdown(editor: LexicalEditor): string {
  let out = "";
  editor.getEditorState().read(() => {
    out = $convertToMarkdownString(MD_TRANSFORMERS);
  });
  return out;
}

/** Import then export — used by tests to assert structural round-trip. */
export function roundTripMarkdown(markdown: string): string {
  const editor = createMdEditor();
  importMarkdown(editor, markdown);
  return exportMarkdown(editor);
}
