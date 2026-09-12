/* UEFN Ducky panel Service Worker (direct Remote View).
 *
 * The phone panel runs on the site, not on the PC, so the desktop-owned asset
 * paths it references (plugin-ui/…, user-sounds/…, tool-captures/…, duckies/…,
 * model-files/…) do not exist on this origin. This worker forwards those
 * requests to the controlling page, which fetches the bytes from the desktop
 * over the WebRTC blob channel and answers on a MessageChannel port.
 *
 * Scope is whatever directory this file was served from — "/" on the desktop,
 * the plugin's panel directory on the site. Every URL the panel builds for a
 * desktop asset goes through `assetUrl()` so it lands inside that scope; the
 * worker strips the scope prefix again before asking the desktop, so the
 * desktop always sees the plain "/plugin-ui/…" path it serves.
 */
const PREFIXES = ["plugin-ui/", "user-sounds/", "tool-captures/", "duckies/", "model-files/"];
const CACHE = "ud-blob-v2";

self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", (ev) => ev.waitUntil(self.clients.claim()));

/** Directory this worker governs, e.g. "/" or "/static/plugins/uefn-ducky/panel/". */
function scopePath() {
  try {
    const p = new URL(self.registration.scope).pathname;
    return p.endsWith("/") ? p : p + "/";
  } catch {
    return "/";
  }
}

/** Desktop-side path for a request in scope, or "" when we should not handle it. */
function desktopPath(url) {
  if (url.origin !== self.location.origin) return "";
  const base = scopePath();
  if (!url.pathname.startsWith(base)) return "";
  const rel = url.pathname.slice(base.length);
  if (!PREFIXES.some((p) => rel.startsWith(p))) return "";
  if (rel.includes("..")) return "";
  return "/" + rel + (url.search || "");
}

async function viaPage(event, key) {
  const client =
    (await self.clients.get(event.clientId).catch(() => null)) ||
    (await self.clients.matchAll({ type: "window", includeUncontrolled: true }))[0];
  if (!client) return new Response("no page", { status: 503 });
  const cache = await caches.open(CACHE);
  const cached = await cache.match(key);
  const etag = cached ? cached.headers.get("ETag") || "" : "";
  const reply = await new Promise((resolve) => {
    const mc = new MessageChannel();
    const timer = setTimeout(() => resolve({ status: 504 }), 60000);
    mc.port1.onmessage = (ev) => {
      clearTimeout(timer);
      resolve(ev.data || { status: 502 });
    };
    client.postMessage({ type: "ud-blob-fetch", path: key, etag }, [mc.port2]);
  });
  if (reply.status === 304 && cached) return cached;
  if (reply.status >= 400 || !reply.body) {
    return cached || new Response(reply.error || "", { status: reply.status || 502 });
  }
  const headers = { "Content-Type": reply.type || "application/octet-stream" };
  if (reply.etag) headers.ETag = reply.etag;
  const res = new Response(reply.body, { status: 200, headers });
  if (reply.etag) cache.put(key, res.clone()).catch(() => {});
  return res;
}

self.addEventListener("fetch", (event) => {
  if (event.request.method !== "GET") return;
  let url;
  try {
    url = new URL(event.request.url);
  } catch {
    return;
  }
  const key = desktopPath(url);
  if (!key) return;
  event.respondWith(viaPage(event, key));
});
