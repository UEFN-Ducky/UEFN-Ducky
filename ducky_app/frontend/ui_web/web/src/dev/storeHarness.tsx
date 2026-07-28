/**
 * DEV-ONLY harness: renders the Store tab against a mocked pywebview bridge so
 * the redesign can be exercised in a plain browser (vite dev, /store-harness.html).
 * Not part of the production build (vite only builds index.html).
 */
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { ConfirmModalProvider } from "../contexts/ConfirmModalContext";
import type { DuckyOSStoreItemDto } from "../types/panel";
import { StoreTab } from "../views/settings/StoreTab";
import "../theme/styles/index.css";

const svg = (body: string, bg: string) =>
  `data:image/svg+xml;base64,${btoa(
    `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64" fill="none"><rect width="64" height="64" rx="14" fill="${bg}"/>${body}</svg>`,
  )}`;

const ICONS = {
  anthropic: svg(
    '<path fill="#d4a27f" d="M22 46L32 18l10 28h-5.2l-1.6-4.6H28.8L27.2 46H22zm8.4-9.2h3.2L32 27.4l-1.6 9.4z"/>',
    "#18181b",
  ),
  google: svg('<circle cx="32" cy="32" r="14" fill="#4285F4"/><circle cx="32" cy="32" r="6" fill="#fff"/>', "#fff"),
  openai: svg(
    '<circle cx="32" cy="32" r="16" stroke="#10a37f" stroke-width="3"/><circle cx="32" cy="32" r="5" fill="#10a37f"/>',
    "#0d0d0d",
  ),
  discord: svg('<circle cx="26" cy="30" r="4" fill="#fff"/><circle cx="38" cy="30" r="4" fill="#fff"/>', "#5865F2"),
  translation: svg(
    '<circle cx="32" cy="32" r="16" stroke="#fff" stroke-width="3" fill="none"/><path stroke="#fff" stroke-width="2.5" d="M16 32h32M18.5 24h27M18.5 40h27"/>',
    "#2D6A9F",
  ),
  browser: svg(
    '<circle cx="32" cy="32" r="18" stroke="#f5c451" stroke-width="3" fill="none"/><path d="M14 32h36" stroke="#f5c451" stroke-width="3"/>',
    "#101014",
  ),
};

function item(partial: Partial<DuckyOSStoreItemDto> & { slug: string }): DuckyOSStoreItemDto {
  return {
    kind: "plugin",
    category: "plugins",
    categories: ["plugins"],
    tags: [],
    latest_version: "1",
    installed_version: null,
    state: "available",
    enabled: null,
    source: null,
    install_count: 0,
    icon_data_url: null,
    price_cents: 0,
    currency: "usd",
    paid: false,
    owned: null,
    contributes_summary: [],
    ...partial,
  } as DuckyOSStoreItemDto;
}

let catalog: DuckyOSStoreItemDto[] = [
  item({
    slug: "translation",
    name: "Translation",
    description:
      "Live-translate the UEFN Ducky UI into any language using your configured AI. Strings are cached so each phrase is translated once.",
    install_count: 28,
    latest_version: "39",
    installed_version: 37,
    state: "update",
    enabled: true,
    source: "store",
    icon_data_url: ICONS.translation,
    contributes_summary: ["ui", "tools"],
  }),
  item({
    slug: "anthropic",
    name: "Anthropic",
    description:
      "Anthropic API key and Claude Code coding agent. After Install + Enable, Anthropic appears under Settings → LLMs → Providers & Keys, and Claude Code under Coding Agents (Detect runs on install).",
    categories: ["plugins", "gateways"],
    tags: ["anthropic", "claude", "claude-code", "llm", "gateway", "api-key"],
    latest_version: "5",
    install_count: 96,
    icon_data_url: ICONS.anthropic,
    contributes_summary: ["gateway"],
  }),
  item({
    slug: "google",
    name: "Google",
    description:
      "Google Gemini API key and Gemini CLI coding agent. After Install + Enable, Google appears under Settings → LLMs → Providers & Keys, and Gemini CLI under Coding Agents (Detect runs on install).",
    categories: ["plugins", "gateways"],
    tags: ["google", "gemini", "gemini-cli", "llm", "api-key"],
    latest_version: "4",
    install_count: 124_000,
    icon_data_url: ICONS.google,
    contributes_summary: ["gateway"],
  }),
  item({
    slug: "openai",
    name: "OpenAI",
    description:
      "OpenAI API key and Codex coding agent. After Install + Enable, OpenAI appears under Settings → LLMs → Providers & Keys, and Codex under Coding Agents.",
    categories: ["plugins", "gateways"],
    tags: ["openai", "codex", "llm", "gateway"],
    latest_version: "4",
    install_count: 350_000,
    icon_data_url: ICONS.openai,
    contributes_summary: ["gateway"],
  }),
  item({
    slug: "browser",
    name: "Web Browser",
    description:
      "Real Chromium browser tab inside UEFN Ducky — docs, dashboards, search. Uses the app's native WebView2 pane, so sites that block iframes still work.",
    latest_version: "5",
    installed_version: 5,
    state: "installed",
    enabled: true,
    source: "store",
    install_count: 4200,
    icon_data_url: ICONS.browser,
    contributes_summary: ["ui"],
  }),
  item({
    slug: "galaxycraft",
    name: "Galaxy Craft",
    description:
      "Full StarCraft / galaxy-editor chrome swap: command console frame, orbiting planets, animated sidebar units.",
    categories: ["themes"],
    latest_version: "13",
    installed_version: 13,
    state: "installed",
    enabled: false,
    source: "store",
    install_count: 13,
    contributes_summary: ["themes", "effects", "sounds"],
  }),
  item({
    slug: "discord",
    name: "Discord",
    description: "Discord bot chat, !ducky commands, and agent tools in UEFN Ducky",
    latest_version: "6",
    install_count: 890,
    icon_data_url: ICONS.discord,
    contributes_summary: ["tools", "ui"],
  }),
  item({
    slug: "warcraft-pro",
    name: "Warcraft Theme Pro",
    description: "Full warcraft chrome port with faction accents, custom sounds and animated rails.",
    categories: ["themes"],
    latest_version: "2",
    install_count: 56,
    paid: true,
    price_cents: 500,
  }),
  item({
    slug: "ducktactoe",
    name: "Duck-Tac-Toe",
    description:
      "Play tic-tac-toe live with your Ducky in chat — board panel, MCP tools, and a skill that teaches the agent to wait for your clicks and move in real time.",
    categories: ["plugins", "games"],
    latest_version: "2",
    install_count: 2,
  }),
  item({
    slug: "verse-basics",
    name: "Verse Basics",
    kind: "skill",
    category: "skills",
    categories: ["skills"],
    description: "Skill pack: Verse syntax, device wiring patterns and digest search recipes.",
    latest_version: "3",
    install_count: 300,
  }),
  item({
    slug: "boss-fights",
    name: "Boss Fight Patterns",
    kind: "skill",
    category: "skills",
    categories: ["skills"],
    description: "Skill pack of boss phase machines, health gates and arena scripting.",
    latest_version: "1",
    installed_version: 1,
    state: "installed",
    install_count: 77,
  }),
  item({
    slug: "hacker",
    name: "Hacker",
    description: "Matrix-green terminal theme with fullscreen rain effect. Appearance + Matrix FX in one plugin.",
    categories: ["themes"],
    latest_version: "2",
    install_count: 41,
  }),
  item({
    slug: "piper",
    name: "Pipe",
    description:
      "Real neural voices that run 100% on your PC — no API key, offline, free forever. Powered by Piper.",
    latest_version: "4",
    install_count: 1500,
  }),
  item({
    slug: "sidegrade",
    name: "Sideloaded Sample",
    description: "A locally sideloaded plugin zip (untrusted until enabled once).",
    latest_version: "1",
    installed_version: 1,
    state: "installed",
    enabled: false,
    source: "local",
    install_count: null as unknown as number,
  }),
];

const jobs = new Map<string, { done: boolean; result: unknown; started: number }>();
let jobSeq = 0;

const mockApi = {
  get_listener_status: async () => ({ ok: true }),
  duckyos_get_status: async () => ({ ok: true, logged_in: false, email: "" }),
  duckyos_store_catalog: async () => ({ ok: true, items: catalog.map((i) => ({ ...i })) }),
  duckyos_store_checkout: async () => ({ ok: false, error: "Checkout is mocked in the harness" }),
  bridge_job_start: async (method: string, args: unknown[]) => {
    const id = `job-${++jobSeq}`;
    jobs.set(id, { done: false, result: null, started: Date.now() });
    if (method === "duckyos_store_download") {
      const [slug] = args as [string, string, boolean];
      setTimeout(() => {
        catalog = catalog.map((i) =>
          i.slug === slug
            ? {
                ...i,
                state: "installed",
                installed_version: Number(i.latest_version || 1),
                enabled: i.enabled ?? true,
                source: "store",
              }
            : i,
        );
        jobs.set(id, {
          done: true,
          result: { ok: true, version: catalog.find((i) => i.slug === slug)?.latest_version },
          started: 0,
        });
      }, 1800);
    } else {
      jobs.set(id, { done: true, result: { ok: false, error: `unknown job ${method}` }, started: 0 });
    }
    return { ok: true, job_id: id };
  },
  bridge_job_poll: async (id: string) => {
    const job = jobs.get(id);
    if (!job) return { ok: false, error: "no such job" };
    if (!job.done) return { ok: true, done: false };
    return { ok: true, done: true, result: job.result };
  },
  set_uefn_plugin_enabled: async (slug: string, enabled: boolean, trustLocal?: boolean) => {
    const target = catalog.find((i) => i.slug === slug);
    if (target?.source === "local" && enabled && !trustLocal) {
      return { ok: false, needs_trust: true, error: "Unofficial local plugin — enable anyway?" };
    }
    catalog = catalog.map((i) => (i.slug === slug ? { ...i, enabled } : i));
    return { ok: true };
  },
  set_skill_pack_enabled: async (slug: string, enabled: boolean) => {
    catalog = catalog.map((i) => (i.slug === slug ? { ...i, enabled } : i));
    return { ok: true, pack_id: slug, enabled };
  },
  delete_skill_pack: async (slug: string) => {
    catalog = catalog.map((i) =>
      i.slug === slug
        ? { ...i, state: "available", installed_version: null, enabled: null, source: null }
        : i,
    );
    return { ok: true, filename: slug, pack_id: slug };
  },
  get_uefn_plugin_secret_labels: async (slug: string) => ({
    ok: true,
    labels: slug === "browser" ? ["Saved logins (WebView2 profile)"] : [],
  }),
  uninstall_uefn_plugin: async (slug: string) => {
    catalog = catalog.map((i) =>
      i.slug === slug
        ? { ...i, state: "available", installed_version: null, enabled: null, source: null }
        : i,
    );
    return { ok: true };
  },
  install_uefn_plugin_bytes: async () => ({ ok: false, error: "Sideload is mocked in the harness" }),
  open_uefn_plugins_folder: async () => ({ ok: true }),
};

(window as unknown as { pywebview: unknown }).pywebview = { api: mockApi };

const rootEl = document.getElementById("root")!;
createRoot(rootEl).render(
  <StrictMode>
    <ConfirmModalProvider>
      <div className="settings-view-content harness-shell">
        <StoreTab />
      </div>
    </ConfirmModalProvider>
  </StrictMode>,
);
