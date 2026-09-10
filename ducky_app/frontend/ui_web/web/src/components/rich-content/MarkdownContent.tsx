import { Children, isValidElement, useMemo } from "react";
import type { ReactNode } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import type { Components } from "react-markdown";
import type { OpenFileHandler } from "../../types/richContent";
import { RichLink } from "./RichInline";
import { promoteMarkdownToSegments } from "./promoteMarkdownBlocks";
import { RichBlockView } from "./RichBlockList";
import { RichCodeBlock } from "./RichCodeBlock";
import { RichHeading } from "./RichHeading";
import { RichParagraph } from "./RichParagraph";
import { RichEmphasis } from "./RichEmphasis";
import { richNodeText, richTextClass, richUrlTransform } from "./richTextColors";

interface MarkdownContentProps {
  text: string;
  onOpenFile?: OpenFileHandler;
}

function MarkdownChunk({ text, onOpenFile }: MarkdownContentProps) {
  const components = useMemo((): Components => {
    return {
      h1: ({ children }) => <RichHeading level={1}>{children}</RichHeading>,
      h2: ({ children }) => <RichHeading level={2}>{children}</RichHeading>,
      h3: ({ children }) => <RichHeading level={3}>{children}</RichHeading>,
      h4: ({ children }) => <RichHeading level={4}>{children}</RichHeading>,
      p: ({ children }) => <RichParagraph>{children}</RichParagraph>,
      strong: ({ children }) => <RichEmphasis>{children}</RichEmphasis>,
      a: ({ href, children }) => <RichLink href={href} onOpenFile={onOpenFile}>{children}</RichLink>,
      ul: ({ children }) => <ul className="rich-list">{children}</ul>,
      ol: ({ children, start }) => <ol start={start} className="rich-list rich-list--ordered">{children}</ol>,
      li: ({ children }) => {
        const label = richNodeText(children);
        return <li className={`rich-list-item ${label.length <= 48 ? richTextClass(label) : ""}`}>{children}</li>;
      },
      blockquote: ({ children }) => <blockquote className="rich-blockquote">{children}</blockquote>,
      code: ({ children }) => <RichCodeBlock text={String(children)} inline onOpenFile={onOpenFile} />,
      pre: ({ children }) => {
        // The pre wrapper identifies blocks even when no language is supplied.
        const child = Children.toArray(children)[0];
        if (!isValidElement<{ children?: ReactNode; className?: string }>(child)) return <pre>{children}</pre>;
        return <RichCodeBlock
          text={String(child.props.children ?? "").replace(/\n$/, "")}
          language={child.props.className?.replace("language-", "")}
        />;
      },
      table: ({ children }) => (
        <div className="rich-table-wrap">
          <table className="rich-table">{children}</table>
        </div>
      ),
      thead: ({ children }) => <thead>{children}</thead>,
      tbody: ({ children }) => <tbody>{children}</tbody>,
      tr: ({ children }) => <tr>{children}</tr>,
      th: ({ children }) => <th className="rich-table-th">{children}</th>,
      td: ({ children }) => <td className="rich-table-td">{children}</td>,
      hr: () => <hr className="rich-hr" />,
    };
  }, [onOpenFile]);

  return (
    <ReactMarkdown remarkPlugins={[remarkGfm]} urlTransform={richUrlTransform} components={components}>
      {text}
    </ReactMarkdown>
  );
}

export function MarkdownContent({ text, onOpenFile }: MarkdownContentProps) {
  const segments = useMemo(() => promoteMarkdownToSegments(text), [text]);
  return (
    <div className="rich-markdown">
      {segments.map((seg, i) =>
        seg.kind === "markdown" ? (
          <MarkdownChunk key={`md-${i}`} text={seg.text} onOpenFile={onOpenFile} />
        ) : (
          <RichBlockView
            key={`blk-${i}`}
            block={seg.block}
            onOpenFile={onOpenFile}
            collapsePath={`md:${i}:`}
          />
        ),
      )}
    </div>
  );
}
