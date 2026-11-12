/** Claude Code login modal — stop saying "waiting" after a hard fail. */

export function loginLinkPrompt(opts: {
  starting: boolean;
  authUrl: string;
  error: string;
}): string {
  if (opts.authUrl.trim()) return "";
  if (opts.starting) return "Getting the sign-in link…";
  if (opts.error.trim()) return "";
  return "Waiting for the sign-in link…";
}

/** Shown when a link was pasted where the code goes (Nov 10 2026 feedback: "pasting the code didn't work"). */
export const PASTED_A_LINK =
  "That is a link, not the code. Sign in at the link above, then paste the code Claude shows you here.";

/**
 * What goes to the CLI from the code box. Claude's code is `code#state`: both halves are
 * needed, so nothing is cut from it. Removed: spaces and line breaks a copy brought along,
 * and quotes around it. A pasted callback link (`...?code=X&state=Y`) becomes `X#Y`.
 * Any other link is not a code: `{ code: "", error }`.
 */
export function cleanLoginCode(raw: string): { code: string; error: string } {
  const text = raw.trim().replace(/^["'`]+|["'`]+$/g, "").trim();
  if (/^https?:\/\//i.test(text)) {
    try {
      const params = new URL(text.replace(/\s+/g, "")).searchParams;
      const code = (params.get("code") || "").trim();
      const state = (params.get("state") || "").trim();
      if (code) return { code: state ? `${code}#${state}` : code, error: "" };
    } catch {
      /* not a link the browser can read: same answer as a link with no code */
    }
    return { code: "", error: PASTED_A_LINK };
  }
  return { code: text.replace(/\s+/g, ""), error: "" };
}

export function loginCanRetry(opts: {
  starting: boolean;
  authUrl: string;
  error: string;
}): boolean {
  return !opts.starting && !opts.authUrl.trim() && Boolean(opts.error.trim());
}
