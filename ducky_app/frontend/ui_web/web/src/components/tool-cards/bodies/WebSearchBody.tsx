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
  results?: WebRow[];
  images?: WebImage[];
};

function parsePayload(resultText: string): WebPayload | null {
  const raw = (resultText || "").trim();
  if (!raw) return null;
  try {
    const parsed = JSON.parse(raw) as WebPayload;
    return parsed && typeof parsed === "object" ? parsed : null;
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
  const query = typeof args.query === "string" ? args.query.trim() : String(payload?.query || "").trim();
  const fetchUrl = typeof args.url === "string" ? args.url.trim() : "";
  const isFetch = toolName === "web_fetch";

  if (payload?.need_permission) {
    return <div className="tool-card-web-note">Waiting for permission to search the web.</div>;
  }
  if (isError || payload?.ok === false) {
    const raw = (resultText || "").trim();
    const message = String(payload?.error || (payload ? "" : raw) || "Web lookup failed.").trim().slice(0, 400);
    return <div className="tool-card-web-note tool-card-web-note--error">{message}</div>;
  }

  if (isFetch) {
    const title = String(payload?.title || "").trim() || hostOf(safeWebHref(payload?.url || fetchUrl)) || "Page";
    const url = String(payload?.url || fetchUrl);
    const excerpt = String(payload?.excerpt || "").trim();
    return (
      <div className="tool-card-web">
        <ResultLink url={url} title={title} />
        {safeWebHref(url) ? <div className="tool-card-web-host">{hostOf(safeWebHref(url))}</div> : null}
        {excerpt ? <p className="tool-card-web-snippet">{excerpt}</p> : null}
      </div>
    );
  }

  const rows = Array.isArray(payload?.results) ? payload.results : [];
  const images = (Array.isArray(payload?.images) ? payload.images : []).filter((image) =>
    safeImageSrc(String(image?.thumb || "")),
  );
  return (
    <div className="tool-card-web">
      {query ? (
        <div className="tool-card-web-query">
          Searching the web for <strong>{query}</strong>
        </div>
      ) : null}
      {images.length > 0 ? (
        <div className="tool-card-web-images">
          {images.map((image, index) => (
            <Picture key={`${image.thumb}-${index}`} image={image} />
          ))}
        </div>
      ) : null}
      {rows.length === 0 ? (
        <div className="tool-card-web-note">No results.</div>
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
