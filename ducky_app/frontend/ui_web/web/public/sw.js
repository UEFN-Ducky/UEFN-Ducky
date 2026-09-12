/* UEFN Ducky panel Service Worker (direct Remote View).
 *
 * The phone panel runs on the panel host, not on the PC, so relative asset
 * URLs the panel uses (/plugin-ui/…, /user-sounds/…, /tool-captures/…,
 * /duckies/…, /model-files/…) do not exist here. This worker forwards those
 * requests to the controlling page, which fetches the bytes from the desktop
 * over the WebRTC blob channel and answers on a MessageChannel port.
 * Everything else passes through untouched.
 */
const PREFIXES = ["/plugin-ui/", "/user-sounds/", "/tool-captures/", "/duckies/", "/model-files/"];
const CACHE = "ud-blob-v1";

self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", (ev) => ev.waitUntil(self.clients.claim()));

function wants(url) {
  if (url.origin !== self.location.origin) return false;
  return PREFIXES.some((p) => url.pathname.startsWith(p));
}

async function viaPage(event, url) {
  const client = await self.clients.get(event.clientId).catch(() => null);
  const target = client || (await self.clients.matchAll({ type: "window", includeUncontrolled: true }))[0];
  if (!target) return new Response("no page", { status: 503 });
  const cache = await caches.open(CACHE);
  const key = url.pathname + url.search;
  const cached = await cache.match(key);
  const etag = cached ? cached.headers.get("ETag") || "" : "";
  const reply = await new Promise((resolve) => {
    const mc = new MessageChannel();
    const timer = setTimeout(() => resolve({ status: 504 }), 60000);
    mc.port1.onmessage = (ev) => {
      clearTimeout(timer);
      resolve(ev.data || { status: 502 });
    };
    target.postMessage({ type: "ud-blob-fetch", path: key, etag }, [mc.port2]);
  });
  if (reply.status === 304 && cached) return cached;
  if (reply.status >= 400 || !reply.body) return cached || new Response(reply.error || "", { status: reply.status || 502 });
  const headers = { "Content-Type": reply.type || "application/octet-stream" };
  if (reply.etag) headers.ETag = reply.etag;
  const res = new Response(reply.body, { status: 200, headers });
  if (reply.etag) cache.put(key, res.clone()).catch(() => {});
  return res;
}

self.addEventListener("fetch", (event) => {
  let url;
  try {
    url = new URL(event.request.url);
  } catch {
    return;
  }
  if (event.request.method !== "GET" || !wants(url)) return;
  event.respondWith(viaPage(event, url));
});
