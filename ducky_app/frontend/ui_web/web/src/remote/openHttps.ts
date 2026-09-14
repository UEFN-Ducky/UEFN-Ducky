/** Open an https URL in this browser (phone /ducky iframe), not the PC. */

export const UD_OPEN_URL = "ud-open-url";
const PARENT_ORIGIN = "https://uefnducky.org";

export function httpsUrl(url: string): string {
  const u = String(url || "").trim();
  return /^https:\/\//i.test(u) ? u : "";
}

/** Same-click opener so iOS keeps the user gesture. Framed → also ask /ducky. */
export function openHttpsOnThisDevice(url: string): boolean {
  const u = httpsUrl(url);
  if (!u || typeof window === "undefined") return false;
  if (window.parent !== window) {
    window.parent.postMessage({ type: UD_OPEN_URL, url: u }, PARENT_ORIGIN);
  }
  window.open(u, "_blank", "noopener,noreferrer");
  return true;
}
