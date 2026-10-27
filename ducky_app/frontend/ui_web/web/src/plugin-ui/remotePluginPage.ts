/**
 * A plugin page for the phone panel, built from the desktop's files over the PC link.
 *
 * Plugin iframes are sandboxed without `allow-same-origin` (an opaque origin), and
 * browsers never route such a frame's loads through the panel's Service Worker. So on
 * the site the page and every file it loads went to the site itself (404, and
 * `frame-ancestors 'none'` blocked the frame). Instead the panel fetches the page and
 * its files over the blob channel and hands the frame one self-contained document:
 *
 * - every `<script>` (the host's injected ones included) becomes a `blob:` script: the
 *   frame inherits the site's CSP, which runs `blob:` scripts but no inline ones;
 * - each stylesheet becomes a `<style>` (inline styles are allowed), its `url()`s as
 *   `data:` URLs (fonts may only be `data:` there);
 * - images and media get `blob:` URLs;
 * - the UI kit's `ducky.js` links `ducky.css` beside its own URL, which a `blob:` script
 *   doesn't have, so the kit's stylesheet goes in as a `<style data-ducky-kit>` ahead of it.
 *
 * Limits: files a page loads later from script (`fetch("./x.json")`, `img.src = "y.png"`,
 * ES module imports) are not rewritten; plugin pages talk to the host over postMessage.
 */

/** Fetch one desktop path over the link (``DirectTransport.fetchBlob``). */
export type FetchDesktop = (path: string) => Promise<Response>;

export type RemotePluginPage = { url: string; revoke: () => void };

const DESKTOP = "https://desktop.invalid";
const JS_TYPES = new Set(["", "text/javascript", "application/javascript", "module", "text/ecmascript"]);
const MEDIA_ATTRS: [string, string][] = [
  ["img", "src"],
  ["source", "src"],
  ["video", "src"],
  ["video", "poster"],
  ["audio", "src"],
  ["link[rel~='icon']", "href"],
];

/** The desktop path ``ref`` points to from ``base`` (a desktop path), or "" when it is not
 * one of the desktop's plugin files (absolute, data:, blob:, a fragment, or outside). */
export function desktopPathFor(ref: string, base: string): string {
  const raw = String(ref || "").trim();
  if (!raw || raw.startsWith("#") || /^(data|blob|javascript|mailto|about):/i.test(raw)) return "";
  let url: URL;
  try {
    url = new URL(raw, DESKTOP + base);
  } catch {
    return "";
  }
  if (url.origin !== DESKTOP) return "";
  const path = url.pathname;
  if (!path.startsWith("/plugin-ui/") || path.includes("..")) return "";
  return path + url.search;
}

/** Desktop path of a plugin's page: ``/plugin-ui/<plugin>/<entry>``. */
export function pluginPagePath(pluginId: string, entry: string): string {
  return `/plugin-ui/${pluginId}/${String(entry || "").replace(/^\/+/, "")}`;
}

/** Where a plugin frame's page comes from: the panel server's URL on the desktop
 * (``assetUrl``), or, with a PC link (the phone panel, another Ducky's window), the
 * desktop path to build over the link. */
export type PluginPageSource = { kind: "local"; url: string } | { kind: "link"; path: string };

export function pluginPageSource(
  pluginId: string,
  entry: string,
  linked: boolean,
  assetUrl: (path: string) => string,
): PluginPageSource {
  const path = pluginPagePath(pluginId, entry);
  return linked ? { kind: "link", path } : { kind: "local", url: assetUrl(path.slice(1)) };
}

function base64(bytes: Uint8Array): string {
  let s = "";
  for (let i = 0; i < bytes.length; i += 0x8000) s += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
  return btoa(s);
}

async function ok(fetchDesktop: FetchDesktop, path: string): Promise<Response> {
  const res = await fetchDesktop(path);
  if (res.status >= 400) throw new Error(`${path}: ${res.status}`);
  return res;
}

/** ``css`` with each ``url()`` it can resolve on the desktop as a data: URL. */
async function inlineCssUrls(css: string, cssPath: string, fetchDesktop: FetchDesktop): Promise<string> {
  const re = /url\(\s*(['"]?)([^'")]+)\1\s*\)/g;
  const refs = new Map<string, string>();
  for (const m of css.matchAll(re)) {
    const path = desktopPathFor(m[2], cssPath);
    if (path) refs.set(m[2], path);
  }
  const data = new Map<string, string>();
  await Promise.all(
    [...refs].map(async ([ref, path]) => {
      try {
        const res = await ok(fetchDesktop, path);
        const type = (res.headers.get("Content-Type") || "application/octet-stream").split(";")[0];
        data.set(ref, `data:${type};base64,${base64(new Uint8Array(await res.arrayBuffer()))}`);
      } catch {
        /* a missing font or picture: the rule just has nothing to show */
      }
    }),
  );
  return css.replace(re, (whole, _q, ref: string) => (data.has(ref) ? `url("${data.get(ref)}")` : whole));
}

/**
 * Build the page at ``pagePath`` (``/plugin-ui/<plugin>/<entry>``) as one blob: document.
 * ``makeUrl`` / ``dropUrl`` are the blob URL calls (test seams).
 */
export async function buildRemotePluginPage(
  pagePath: string,
  fetchDesktop: FetchDesktop,
  makeUrl: (blob: Blob) => string = (b) => URL.createObjectURL(b),
  dropUrl: (url: string) => void = (u) => URL.revokeObjectURL(u),
): Promise<RemotePluginPage> {
  const made: string[] = [];
  const blobUrl = (parts: BlobPart[], type: string) => {
    const url = makeUrl(new Blob(parts, { type }));
    made.push(url);
    return url;
  };
  const revoke = () => made.splice(0).forEach((u) => dropUrl(u));
  try {
    const page = await ok(fetchDesktop, pagePath);
    const doc = new DOMParser().parseFromString(await page.text(), "text/html");
    const jobs: Promise<void>[] = [];

    for (const script of Array.from(doc.querySelectorAll("script"))) {
      const type = (script.getAttribute("type") || "").trim().toLowerCase();
      if (!JS_TYPES.has(type)) continue; // JSON data blocks and templates stay as they are
      script.removeAttribute("integrity");
      script.removeAttribute("crossorigin");
      const src = script.getAttribute("src");
      if (src === null) {
        // Inline (the host's error reporter and live script, or the plugin's own).
        script.setAttribute("src", blobUrl([script.textContent || ""], "text/javascript"));
        script.textContent = "";
        continue;
      }
      const path = desktopPathFor(src, pagePath);
      if (!path) continue;
      if (/\/_kit\/ducky\.js(\?|$)/.test(path)) {
        const cssPath = path.replace(/ducky\.js(\?.*)?$/, "ducky.css");
        const style = doc.createElement("style");
        style.setAttribute("data-ducky-kit", "");
        script.before(style);
        jobs.push(
          ok(fetchDesktop, cssPath).then(async (res) => {
            style.textContent = await inlineCssUrls(await res.text(), cssPath, fetchDesktop);
          }, () => {
            /* no kit stylesheet: the page still opens, unstyled */
          }),
        );
      }
      jobs.push(
        ok(fetchDesktop, path).then(async (res) => {
          script.setAttribute("src", blobUrl([await res.arrayBuffer()], "text/javascript"));
        }),
      );
    }

    for (const link of Array.from(doc.querySelectorAll("link[rel~='stylesheet'][href]"))) {
      const path = desktopPathFor(link.getAttribute("href") || "", pagePath);
      if (!path) continue;
      jobs.push(
        ok(fetchDesktop, path).then(async (res) => {
          const style = doc.createElement("style");
          const media = link.getAttribute("media");
          if (media) style.setAttribute("media", media);
          style.textContent = await inlineCssUrls(await res.text(), path, fetchDesktop);
          link.replaceWith(style);
        }),
      );
    }

    for (const [selector, attr] of MEDIA_ATTRS) {
      for (const el of Array.from(doc.querySelectorAll(`${selector}[${attr}]`))) {
        const path = desktopPathFor(el.getAttribute(attr) || "", pagePath);
        if (!path) continue;
        jobs.push(
          ok(fetchDesktop, path).then(
            async (res) => {
              const type = (res.headers.get("Content-Type") || "").split(";")[0];
              el.setAttribute(attr, blobUrl([await res.arrayBuffer()], type));
            },
            () => {
              /* a missing picture shows as broken, the page still loads */
            },
          ),
        );
      }
    }

    for (const el of Array.from(doc.querySelectorAll("style"))) {
      const css = el.textContent || "";
      if (css.includes("url(")) {
        jobs.push(inlineCssUrls(css, pagePath, fetchDesktop).then((out) => void (el.textContent = out)));
      }
    }

    await Promise.all(jobs);
    const html = `<!doctype html>\n${doc.documentElement.outerHTML}`;
    return { url: blobUrl([html], "text/html;charset=utf-8"), revoke };
  } catch (err) {
    revoke();
    throw err;
  }
}
