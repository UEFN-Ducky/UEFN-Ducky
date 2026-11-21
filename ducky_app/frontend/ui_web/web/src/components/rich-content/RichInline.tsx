import type { ReactNode } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import type { OpenFileHandler } from "../../types/richContent";
import { basename } from "../../verse-editor/utils/isVerseFile";
import { isWorkspaceFilePath, localFilePath, normalizeWorkspacePath } from "./isWorkspacePath";
import { RichCodeBlock } from "./RichCodeBlock";
import { onActivateKey } from "./RichCodeChip";
import { RichEmphasis } from "./RichEmphasis";
import { richColorFromHref, richUrlTransform } from "./richTextColors";
import { parseChatRefHref } from "../chatReferences";
import { requestOpenChatReference } from "../../navigation/openChatReference";
import { ChatRefChip } from "../ChatRefChip";
import { openCodingAgentLoginUi, parseCodingAgentLoginHref } from "../../walkthrough/openCodingAgentLogin";
import { getApi, isRemote } from "../../hooks/usePanelApi";
import { openHttpsOnThisDevice } from "../../remote/openHttps";

export function RichLink({ href = "", children, onOpenFile }: {
  href?: string;
  children?: ReactNode;
  onOpenFile?: OpenFileHandler;
}) {
  const color = richColorFromHref(href);
  if (color) return <span className={`rich-text-accent rich-tone--${color}`}>{children}</span>;
  // A full path on this PC (an export, a file in another folder): show it in File Explorer.
  // Before the reference chips, whose `file:` form is a project file. Not on a phone: the
  // Explorer window would open on the PC.
  const local = localFilePath(href);
  if (local) {
    if (isRemote()) return <span className="rich-md-link rich-md-link--static" title={local}>{children}</span>;
    const run = () => void getApi()?.open_path_in_explorer?.(local);
    return (
      <span role="button" tabIndex={0} className="rich-md-link" title={`Show in File Explorer: ${local}`} onClick={run} onKeyDown={onActivateKey(run)}>
        {children}
      </span>
    );
  }
  if (parseChatRefHref(href)) {
    const label = (Array.isArray(children) ? children : [children])
      .map((child) => (typeof child === "string" || typeof child === "number" ? String(child) : ""))
      .join("");
    return (
      <ChatRefChip
        href={href}
        label={label}
        onClick={(event) => {
          event.preventDefault();
          event.stopPropagation();
          requestOpenChatReference(href, label);
        }}
      />
    );
  }
  if (href.startsWith("plan-node:")) {
    const id = href.slice("plan-node:".length).trim();
    return id ? <span className="plan-md-anchor" data-plan-node-id={id} title="Linked plan step">{children}</span> : null;
  }
  const login = parseCodingAgentLoginHref(href);
  // Inline spans, not <button>s, so a long link wraps left-aligned with its sentence.
  if (login) {
    const run = () => void openCodingAgentLoginUi({ providerId: login.providerId });
    return (
      <span
        role="button"
        tabIndex={0}
        className="rich-md-link"
        title="Open Settings → LLMs and highlight Log in"
        onClick={run}
        onKeyDown={onActivateKey(run)}
      >
        {children}
      </span>
    );
  }
  if (isWorkspaceFilePath(href)) {
    const path = normalizeWorkspacePath(href);
    // No editor handler here (a plan, a card): the app opens the file itself.
    const run = () => (onOpenFile ? onOpenFile(path, basename(path)) : void getApi()?.open_project_file(path));
    return (
      <span role="button" tabIndex={0} className="rich-md-link" title={path} onClick={run} onKeyDown={onActivateKey(run)}>
        {children}
      </span>
    );
  }
  if (/^https?:\/\//i.test(href)) {
    const remoteHttps = isRemote() && /^https:\/\//i.test(href);
    return (
      <a
        href={href}
        className="rich-md-link rich-md-link--external"
        target="_blank"
        rel="noreferrer"
        onClick={remoteHttps ? (ev) => { ev.preventDefault(); openHttpsOnThisDevice(href); } : undefined}
      >
        {children}
      </a>
    );
  }
  return <span className="rich-md-link rich-md-link--static">{children}</span>;
}

/** The same safe emphasis, badges and file links inside every structured block. */
export function RichInline({ text, onOpenFile }: { text: string; onOpenFile?: OpenFileHandler }) {
  return (
    <ReactMarkdown
      remarkPlugins={[remarkGfm]}
      urlTransform={richUrlTransform}
      allowedElements={["p", "strong", "em", "code", "a", "del", "br", "img"]}
      unwrapDisallowed
      skipHtml
      components={{
        p: ({ children }) => <>{children}</>,
        strong: ({ children }) => <RichEmphasis>{children}</RichEmphasis>,
        code: ({ children }) => <RichCodeBlock text={String(children)} inline onOpenFile={onOpenFile} />,
        a: ({ href, children }) => <RichLink href={href} onOpenFile={onOpenFile}>{children}</RichLink>,
        img: ({ alt }) => (alt ? <span>{alt}</span> : null),
      }}
    >
      {text}
    </ReactMarkdown>
  );
}
