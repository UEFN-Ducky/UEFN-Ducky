import type { ToolCardBodyProps } from "../toolCardTypes";

type WebRow = { title: string; url: string; snippet: string };
type WebImage = { title: string; url: string; thumb: string };

type WebPayload = {
  ok?: boolean;
  error?: string;
  need_permission?: boolean;
  query?: string;
  title?: string;
  url?: string;
  excerpt?: string;
  text?: string;
  results?: WebRow[];
  sources?: WebRow[];
  images?: WebImage[];
};

function parsePayload(resultText: string): WebPayload | null {
  const raw = (resultText || "").trim();
  if (!raw) return null;
  try {
    const parsed = JSON.parse(raw) as WebPayload;
    return parsed && typeof parsed === "object" && !Array.isArray(parsed) ? parsed : null;
  } catch {
    // The chat transcript caps long tool text. Results sit before the page body,
    // so a cut inside "text" still leaves a readable card.
    const cut = raw.indexOf(',"text":');
    if (cut <= 0) return null;
    try {
      const parsed = JSON.parse(raw.slice(0, cut) + "}") as WebPayload;
      return parsed && typeof parsed === "object" ? parsed : null;
    } catch {
      return null;
    }
  }
}

/** Only http(s) links the tool already checked. Anything else is plain text. */
export function safeWebHref(raw: string): string {
  const text = (raw || "").trim();
  if (!text || /\s/.test(text)) return "";
  try {
    const url = new URL(text);
    if (url.protocol !== "https:" && url.protocol !== "http:") return "";
    if (url.username || url.password) return "";
    return url.toString();
  } catch {
    return "";
  }
}

/** Picture src for the chat card. https only — the card displays it, it is not saved. */
export function safeImageSrc(raw: string): string {
  const href = safeWebHref(raw);
  return href.startsWith("https:") ? href : "";
}

function hostOf(href: string): string {
  try {
    return new URL(href).host;
  } catch {
    return "";
  }
}

function Picture({ image }: { image: WebImage }) {
  const src = safeImageSrc(image.thumb);
  const href = safeWebHref(image.url);
  if (!src) return null;
  const img = (
    <img
      className="tool-card-web-thumb"
      src={src}
      alt={image.title || ""}
      loading="lazy"
      referrerPolicy="no-referrer"
    />
  );
  if (!href) return img;
  return (
    <a className="tool-card-web-thumb-link" href={href} target="_blank" rel="noopener noreferrer">
      {img}
    </a>
  );
}

function ResultLink({ url, title }: { url: string; title: string }) {
  const href = safeWebHref(url);
  if (!href) return <span className="tool-card-web-title">{title}</span>;
  return (
    <a className="tool-card-web-link" href={href} target="_blank" rel="noopener noreferrer">
      {title}
    </a>
  );
}

export function WebSearchBody({
  toolName,
  args,
  resultText,
  isError,
}: ToolCardBodyProps) {
  const payload = parsePayload(resultText);
  const action = args.action && typeof args.action === "object" && !Array.isArray(args.action)
    ? args.action as Record<string, unknown> : {};
  const query = typeof args.query === "string" ? args.query.trim()
    : typeof action.query === "string" ? action.query.trim()
    : Array.isArray(action.queries) ? action.queries.filter((q) => typeof q === "string").join(" · ")
    : String(payload?.query || "").trim();
  const fetchUrl = typeof args.url === "string" ? args.url.trim() : typeof action.url === "string" ? action.url.trim() : "";
  const isFind = action.type === "find_in_page";
  const isFetch = toolName === "web_fetch" || action.type === "open_page" || isFind || Boolean(payload?.url && !Array.isArray(payload.results));
  const pattern = typeof action.pattern === "string" ? action.pattern : "";
  // Native Codex completions can contain only the echoed query. That is not
  // an empty result set, nor is it returned page content.
  const raw = (resultText || "").trim();
  const excerpt = String(payload?.excerpt || payload?.text || "").trim()
    || (!payload && raw !== query ? raw : "");

  if (payload?.need_permission) {
    return <div className="tool-card-web-note">Waiting for permission to search the web.</div>;
  }
  if (isError || payload?.ok === false) {
    const raw = (resultText || "").trim();
    const message = String(payload?.error || (payload ? "" : raw) || "Web lookup failed.").trim().slice(0, 400);
    return <div className="tool-card-web-note tool-card-web-note--error">{message}</div>;
  }

  const title = String(payload?.title || "").trim() || hostOf(safeWebHref(payload?.url || fetchUrl)) || "Page";
  const url = String(payload?.url || fetchUrl);
  const rows = Array.isArray(payload?.results) && payload.results.length ? payload.results
    : Array.isArray(payload?.sources) ? payload.sources : [];
  const images = (Array.isArray(payload?.images) ? payload.images : []).filter((image) =>
    safeImageSrc(String(image?.thumb || "")),
  );
  return (
    <div className="tool-card-web">
      {isFetch ? <ResultLink url={url} title={title} /> : null}
      {isFind && pattern ? <div className="tool-card-web-query">Find in page: <strong>{pattern}</strong></div> : null}
      {query && !isFetch ? (
        <div className="tool-card-web-query">
          Search query: <strong>{query}</strong>
        </div>
      ) : null}
      {images.length > 0 ? (
        <div className="tool-card-web-images">
          {images.map((image, index) => (
            <Picture key={`${image.thumb}-${index}`} image={image} />
          ))}
        </div>
      ) : null}
      {excerpt ? <p className="tool-card-web-snippet">{excerpt}</p> : null}
      {rows.length === 0 ? (
        !excerpt && images.length === 0 ? (
          <div className="tool-card-web-note">
            {!isFetch && Array.isArray(payload?.results) ? "No results." : "Result details unavailable."}
          </div>
        ) : null
      ) : (
        <ul className="tool-card-web-list">
          {rows.map((row, index) => {
            const title = String(row?.title || "").trim();
            const url = String(row?.url || "").trim();
            const snippet = String(row?.snippet || "").trim();
            if (!title && !url) return null;
            return (
              <li key={`${url || title}-${index}`} className="tool-card-web-item">
                <ResultLink url={url} title={title || hostOf(safeWebHref(url)) || url} />
                {safeWebHref(url) ? <div className="tool-card-web-host">{hostOf(safeWebHref(url))}</div> : null}
                {snippet ? <p className="tool-card-web-snippet">{snippet}</p> : null}
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}
