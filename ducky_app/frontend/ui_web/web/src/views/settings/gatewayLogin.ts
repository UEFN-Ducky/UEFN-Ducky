import { getApi } from "../../hooks/usePanelApi";
import type { GatewayAccountStatusDto } from "../../types/panel";

/**
 * Waiting on a gateway's one-click sign-in (`login_gateway_account`): the plugin installs a
 * missing CLI and signs in hidden in the browser, and whoever started it polls the account's
 * status. Settings' accounts table and the setup guide's Connect AI step share this.
 */
export const LOGIN_POLL_MS = 1500;
export const LOGIN_POLLS = 200; // ~5 min, the plugin's own sign-in timeout
export const INSTALLING = "Installing it (about a minute)… the sign-in starts by itself.";
export const REPAIRING = "Repairing Gemini CLI…";
export const BROWSER_WAIT = "Waiting for sign-in in your browser…";
export const NOT_FINISHED = "Sign-in didn't finish, try again.";

/** `alive`: false stops the wait (the row went away). `onWaiting`: what to show meanwhile. */
export type LoginWatch = { alive: () => boolean; onWaiting: (text: string) => void };

const pause = () => new Promise((r) => setTimeout(r, LOGIN_POLL_MS));

async function statusOf(provider: string, id: string): Promise<GatewayAccountStatusDto | null> {
  const st = await getApi()?.gateway_account_status?.(provider, id).catch(() => undefined);
  return st?.ok ? st.status : null;
}

/**
 * A CLI install the plugin is running for this account, waited to its end. `ran` is false
 * when none is running; `ok` when it finished cleanly; `error` is the installer's own line.
 */
export async function waitForInstall(provider: string, id: string, watch: LoginWatch): Promise<{ ran: boolean; ok: boolean; error: string }> {
  const first = await statusOf(provider, id);
  if (!first?.installing) return { ran: false, ok: false, error: "" };
  watch.onWaiting(INSTALLING);
  for (let i = 0; i < LOGIN_POLLS && watch.alive(); i++) {
    await pause();
    const s = await statusOf(provider, id);
    if (s && !s.pending) return { ran: true, ok: !s.error, error: s.error || "" };
  }
  return { ran: true, ok: false, error: "" };
}

/** A hidden browser sign-in, polled to its end: whether it signed in, and the line to show. */
export async function waitForSignIn(provider: string, id: string, waitText: string, watch: LoginWatch): Promise<{ loggedIn: boolean; text: string }> {
  watch.onWaiting(waitText);
  for (let i = 0; i < LOGIN_POLLS && watch.alive(); i++) {
    await pause();
    const s = await statusOf(provider, id);
    if (s?.loggedIn) return { loggedIn: true, text: s.email ? `Logged in · ${s.email}` : "Logged in." };
    if (s && !s.pending) return { loggedIn: false, text: s.error || NOT_FINISHED };
    if (s && watch.alive()) watch.onWaiting(s.repairing ? REPAIRING : waitText);
  }
  return { loggedIn: false, text: NOT_FINISHED };
}
