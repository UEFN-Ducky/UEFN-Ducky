/**
 * Open Settings → LLMs → a gateway slide and spotlight the coding-agent login row.
 * Chat never collects OAuth codes; this is the only login surface.
 */
import { requestOpenSettings } from "../navigation/openSettingsTab";
import { registerTour, startTour, unregisterTour } from "./WalkthroughService";

export const CODING_AGENT_LOGIN_TOUR = "coding_agent.login";

export function parseCodingAgentLoginHref(href: string): { providerId: string } | null {
  const m = /^ducky:\/\/settings\.llms\/([a-z0-9_-]+)#login$/i.exec(String(href || "").trim());
  return m ? { providerId: m[1].toLowerCase() } : null;
}

function wait(ms: number): Promise<void> {
  return new Promise((r) => window.setTimeout(r, ms));
}

function openProvider(providerId: string): void {
  requestOpenSettings("LLMs");
  window.dispatchEvent(
    new CustomEvent("ducky:settings-section", { detail: { tab: "LLMs", section: "llms" } }),
  );
  window.dispatchEvent(new CustomEvent("ducky:llms-select-provider", { detail: { id: providerId } }));
}

export async function openCodingAgentLoginUi(opts?: {
  providerId?: string;
  title?: string;
  body?: string;
}): Promise<void> {
  const providerId = (opts?.providerId || "anthropic").trim().toLowerCase() || "anthropic";
  openProvider(providerId);
  await wait(450);
  unregisterTour(CODING_AGENT_LOGIN_TOUR);
  registerTour({
    id: CODING_AGENT_LOGIN_TOUR,
    title: "Log in",
    persist: false,
    autoStart: "never",
    steps: [
      {
        target: "settings.llms.provider.agent.login",
        title: (opts?.title || "Log in here").trim() || "Log in here",
        body:
          (opts?.body || "").trim() ||
          "Press Log in. Click the sign-in link, then paste the code. The Claude Login tab closes when you cancel or finish.",
        advance: "require_click",
        mode: "rect",
        onEnter: async () => {
          openProvider(providerId);
          await wait(280);
        },
      },
    ],
  });
  await startTour(CODING_AGENT_LOGIN_TOUR, { force: true });
}
