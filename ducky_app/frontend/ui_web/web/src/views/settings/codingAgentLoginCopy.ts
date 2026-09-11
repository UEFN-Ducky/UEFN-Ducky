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

export function loginCanRetry(opts: {
  starting: boolean;
  authUrl: string;
  error: string;
}): boolean {
  return !opts.starting && !opts.authUrl.trim() && Boolean(opts.error.trim());
}
