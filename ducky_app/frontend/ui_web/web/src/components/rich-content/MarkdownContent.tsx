import { useMemo } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import type { Components } from "react-markdown";
import type { OpenFileHandler } from "../../types/richContent";
import { basename } from "../../verse-editor/utils/isVerseFile";
import { isWorkspaceFilePath, normalizeWorkspacePath } from "./isWorkspacePath";
import { RichCodeBlock } from "./RichCodeBlock";
import { RichHeading } from "./RichHeading";
import { RichParagraph } from "./RichParagraph";

interface MarkdownContentProps {
  text: string;
  onOpenFile?: OpenFileHandler;
}

export function MarkdownContent({ text, onOpenFile }: MarkdownContentProps) {
  const components = useMemo((): Components => {
    return {
      h1: ({ children }) => <RichHeading level={1}>{children}</RichHeading>,
      h2: ({ children }) => <RichHeading level={2}>{children}</RichHeading>,
      h3: ({ children }) => <RichHeading level={3}>{children}</RichHeading>,
      h4: ({ children }) => <RichHeading level={4}>{children}</RichHeading>,
      p: ({ children }) => <RichParagraph>{children}</RichParagraph>,
      a: ({ href, children }) => {
        const linkHref = href ?? "";
        if (linkHref.startsWith("plan-node:")) {
          const id = linkHref.slice("plan-node:".length).trim();
          if (!id) return null;
          return (
            <span className="plan-md-anchor" data-plan-node-id={id} title="Linked plan step">
              {children}
            </span>
          );
        }
        if (onOpenFile && isWorkspaceFilePath(linkHref)) {
          const norm = normalizeWorkspacePath(linkHref);
          return (
            <button
              type="button"
              className="rich-md-link"
              onClick={() => onOpenFile(norm, basename(norm))}
              title={norm}
            >
              {children}
            </button>
          );
        }
        if (linkHref.startsWith("http://") || linkHref.startsWith("https://")) {
          return (
            <a href={linkHref} className="rich-md-link rich-md-link--external" target="_blank" rel="noreferrer">
              {children}
            </a>
          );
        }
        return <span className="rich-md-link rich-md-link--static">{children}</span>;
      },
      ul: ({ children }) => <ul className="rich-list">{children}</ul>,
      ol: ({ children }) => <ol className="rich-list rich-list--ordered">{children}</ol>,
      li: ({ children }) => <li className="rich-list-item">{children}</li>,
      blockquote: ({ children }) => <blockquote className="rich-blockquote">{children}</blockquote>,
      code: ({ className, children }) => {
        const isBlock = className?.includes("language-");
        const lang = className?.replace("language-", "") ?? undefined;
        const raw = String(children).replace(/\n$/, "");
        return <RichCodeBlock text={raw} language={lang} inline={!isBlock} />;
      },
      pre: ({ children }) => <div className="rich-pre-wrap">{children}</div>,
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
    <div className="rich-markdown">
      <ReactMarkdown remarkPlugins={[remarkGfm]} components={components}>
        {text}
      </ReactMarkdown>
    </div>
  );
}
