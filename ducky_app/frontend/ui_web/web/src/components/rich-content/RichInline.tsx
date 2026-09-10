import type { ReactNode } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import type { OpenFileHandler } from "../../types/richContent";
import { basename } from "../../verse-editor/utils/isVerseFile";
import { isWorkspaceFilePath, normalizeWorkspacePath } from "./isWorkspacePath";
import { RichCodeBlock } from "./RichCodeBlock";
import { RichEmphasis } from "./RichEmphasis";
import { richColorFromHref, richUrlTransform } from "./richTextColors";
import { openCodingAgentLoginUi, parseCodingAgentLoginHref } from "../../walkthrough/openCodingAgentLogin";

export function RichLink({ href = "", children, onOpenFile }: {
  href?: string;
  children?: ReactNode;
  onOpenFile?: OpenFileHandler;
}) {
  const color = richColorFromHref(href);
  if (color) return <span className={`rich-text-accent rich-tone--${color}`}>{children}</span>;
  if (href.startsWith("plan-node:")) {
    const id = href.slice("plan-node:".length).trim();
    return id ? <span className="plan-md-anchor" data-plan-node-id={id} title="Linked plan step">{children}</span> : null;
  }
  const login = parseCodingAgentLoginHref(href);
  if (login) {
    return (
      <button
        type="button"
        className="rich-md-link"
        title="Open Settings → LLMs and highlight Log in"
        onClick={() => void openCodingAgentLoginUi({ providerId: login.providerId })}
      >
        {children}
      </button>
    );
  }
  if (onOpenFile && isWorkspaceFilePath(href)) {
    const path = normalizeWorkspacePath(href);
    return <button type="button" className="rich-md-link" title={path} onClick={() => onOpenFile(path, basename(path))}>{children}</button>;
  }
  if (/^https?:\/\//i.test(href)) {
    return <a href={href} className="rich-md-link rich-md-link--external" target="_blank" rel="noreferrer">{children}</a>;
  }
  return <span className="rich-md-link rich-md-link--static">{children}</span>;
}

/** The same safe emphasis, badges and file links inside every structured block. */
export function RichInline({ text, onOpenFile }: { text: string; onOpenFile?: OpenFileHandler }) {
  return (
    <ReactMarkdown
      remarkPlugins={[remarkGfm]}
      urlTransform={richUrlTransform}
      allowedElements={["p", "strong", "em", "code", "a", "del", "br"]}
      unwrapDisallowed
      skipHtml
      components={{
        p: ({ children }) => <>{children}</>,
        strong: ({ children }) => <RichEmphasis>{children}</RichEmphasis>,
        code: ({ children }) => <RichCodeBlock text={String(children)} inline onOpenFile={onOpenFile} />,
        a: ({ href, children }) => <RichLink href={href} onOpenFile={onOpenFile}>{children}</RichLink>,
      }}
    >
      {text}
    </ReactMarkdown>
  );
}
