import { requestShowChatComposer } from "../navigation/openChatComposer";
import { requestOpenSettings } from "../navigation/openSettingsTab";
import { getTargetElement, settingsTabTargetId } from "../ui-targets/registry";
import { listEnabledGatewayTours } from "./pluginWalkthroughs";
import {
  ensureStarterLlmGateways,
  markStarterPluginToursCompleted,
  selectLlmsProvider,
  setSuppressStarterPluginTours,
} from "./starterLlmGateways";
import type { WalkthroughDef, WalkthroughStep } from "./types";

function wait(ms: number): Promise<void> {
  return new Promise((r) => window.setTimeout(r, ms));
}

function openTab(label: string): Promise<void> {
  requestOpenSettings(label);
  return wait(350);
}

function fireSection(detail: { tab: string; section: string }): void {
  window.dispatchEvent(new CustomEvent("ducky:settings-section", { detail }));
}

async function openLlmsSection(section: string): Promise<void> {
  await openTab("LLMs");
  fireSection({ tab: "LLMs", section });
  await wait(280);
}

async function openPlansSection(section: string): Promise<void> {
  await openTab("Plans");
  fireSection({ tab: "Plans", section });
  await wait(280);
}

function clickStep(
  target: string,
  title: string,
  body: string,
  onEnter?: () => void | Promise<void>,
): WalkthroughStep {
  return {
    target,
    title,
    body,
    advance: "require_click",
    mode: "rect",
    onEnter,
  };
}

function nextStep(
  target: string,
  title: string,
  body: string,
  onEnter?: () => void | Promise<void>,
): WalkthroughStep {
  return {
    target,
    title,
    body,
    advance: "next",
    mode: "rect",
    onEnter,
  };
}

function settingsGeneralSteps(): WalkthroughStep[] {
  return [
    clickStep(settingsTabTargetId("General"), "General", "Press General in the sidebar to open it.", async () => {
      requestOpenSettings();
      await wait(200);
    }),
    nextStep("settings.content", "General panel", "App updates, Add to UEFN, project files, and exit controls.", () =>
      openTab("General"),
    ),
    clickStep(
      "settings.general.section.general",
      "General",
      "Press General to show this section.",
      async () => {
        await openTab("General");
        fireSection({ tab: "General", section: "general" });
        await wait(200);
      },
    ),
    nextStep(
      "settings.general.app",
      "App Info",
      "Version, update check, and uninstall live here.",
      async () => {
        await openTab("General");
        fireSection({ tab: "General", section: "general" });
        await wait(250);
      },
    ),
    nextStep(
      "settings.general.project_files",
      "Project Files",
      "Controls the sidebar file tree. Engine folders stay hidden by default.",
      async () => {
        await openTab("General");
        fireSection({ tab: "General", section: "general" });
        await wait(250);
      },
    ),
    nextStep(
      "settings.general.add_to_uefn",
      "Add to UEFN",
      "Automatic — no Install button. Enable Python scripting in UEFN, restart, then open your project.",
      async () => {
        await openTab("General");
        fireSection({ tab: "General", section: "general" });
        await wait(250);
      },
    ),
    nextStep(
      "settings.general.app_data",
      "App Data",
      "Open the local App Data folder — settings and cache live here.",
      async () => {
        await openTab("General");
        fireSection({ tab: "General", section: "general" });
        await wait(250);
      },
    ),
    clickStep(
      "settings.general.section.log_errors",
      "Log & Errors",
      "Press Log & Errors to show this section.",
      async () => {
        await openTab("General");
        fireSection({ tab: "General", section: "log_errors" });
        await wait(200);
      },
    ),
    nextStep("settings.content", "Log & Errors", "Last 24 hours of crash and plugin errors only — no chats or personal info. Copy for Discord when reporting a bug.", async () => {
      await openTab("General");
      fireSection({ tab: "General", section: "log_errors" });
      await wait(250);
    }),
    clickStep(
      "settings.log.section.log",
      "Log",
      "Press Log to show this section.",
      async () => {
        await openTab("General");
        fireSection({ tab: "General", section: "log" });
        await wait(200);
      },
    ),
    nextStep("settings.content", "Log", "Live panel log output for debugging.", async () => {
      await openTab("General");
      fireSection({ tab: "General", section: "log" });
      await wait(250);
    }),
    clickStep(
      "settings.log.section.errors",
      "Errors",
      "Press Errors to show this section.",
      async () => {
        await openTab("General");
        fireSection({ tab: "General", section: "errors" });
        await wait(200);
      },
    ),
    nextStep("settings.content", "Errors", "Recent errors captured from the panel and agent.", async () => {
      await openTab("General");
      fireSection({ tab: "General", section: "errors" });
      await wait(250);
    }),
  ];
}

function settingsDuckiesSteps(): WalkthroughStep[] {
  return [
    clickStep(settingsTabTargetId("Duckies"), "Duckies", "Press Duckies to open profiles.", async () => {
      requestOpenSettings();
      await wait(200);
    }),
    nextStep(
      "settings.content",
      "Duckies panel",
      "Create and edit Ducky profiles — personality, skills, and default model for each chat.",
      () => openTab("Duckies"),
    ),
    clickStep(
      "settings.duckies.row.first",
      "Open a Ducky",
      "Press a Ducky card to open its profile editor.",
      () => openTab("Duckies"),
    ),
    clickStep(
      "settings.duckies.section.profile",
      "Profile",
      "Press Profile — name, avatar, personality, and when to use this Ducky.",
      () => openTab("Duckies"),
    ),
    clickStep(
      "settings.duckies.section.skills",
      "Skills",
      "Press Skills — which skill packs this Ducky can use.",
      () => openTab("Duckies"),
    ),
    clickStep(
      "settings.duckies.section.mcps",
      "MCPs",
      "Press MCPs — which tools and MCP servers this Ducky can call.",
      () => openTab("Duckies"),
    ),
    nextStep(
      "settings.duckies.section.memory",
      "Memory",
      "Memory for this ducky is managed here later.",
      () => openTab("Duckies"),
    ),
    clickStep(
      "settings.duckies.back",
      "Back",
      "Press Back to return to the Duckies list.",
      () => openTab("Duckies"),
    ),
  ];
}

function settingsPlansSteps(): WalkthroughStep[] {
  return [
    clickStep(
      settingsTabTargetId("Plans"),
      "Plans",
      "Press Plans to open plan templates and project plans.",
      async () => {
        requestOpenSettings();
        await wait(200);
      },
    ),
    clickStep(
      "settings.plans.section.templates",
      "Plan templates",
      "Press Plan templates to show reusable outlines.",
      async () => {
        await openPlansSection("templates");
      },
    ),
    nextStep(
      "settings.plans.row.demo-getting-started",
      "Getting started",
      "Plans are checklists agents update as they work. This demo template ships with the app.",
      async () => {
        await openPlansSection("templates");
        await wait(350);
      },
    ),
    clickStep(
      "settings.plans.section.working",
      "Working plans",
      "Press Working plans to show active project plans.",
      async () => {
        await openPlansSection("working");
      },
    ),
    nextStep("settings.content", "Working plans", "Active plans for the current project.", async () => {
      await openPlansSection("working");
    }),
  ];
}

function settingsLlmsOverviewSteps(): WalkthroughStep[] {
  return [
    clickStep(settingsTabTargetId("LLMs"), "LLMs", "Press LLMs to open providers, skills, MCPs, and memory.", async () => {
      requestOpenSettings();
      await wait(200);
    }),
    nextStep(
      "settings.content",
      "LLMs panel",
      "Providers and models, plus Skills, MCPs, and Memory in the header tabs.",
      () => openTab("LLMs"),
    ),
    clickStep(
      "settings.llms.section.llms",
      "LLMs",
      "Press LLMs to show providers and models.",
      async () => {
        await openLlmsSection("llms");
      },
    ),
    nextStep(
      "settings.llms.providers",
      "Providers",
      "Press a provider row to slide it open — paste an API key or use Codex / Claude Code, then Test & Save.",
      async () => {
        await openLlmsSection("llms");
        selectLlmsProvider(null);
        await wait(280);
      },
    ),
    clickStep(
      "settings.llms.section.skills",
      "Skills",
      "Press Skills to open skill packs.",
      async () => {
        await openLlmsSection("skills");
      },
    ),
    nextStep(
      "settings.skills.list",
      "Skill packs",
      "Built-in and custom packs. Agents load these as HOW TO guides.",
      async () => {
        await openLlmsSection("skills");
      },
    ),
    clickStep(
      "settings.skills.row.ducky",
      "UEFN Ducky",
      "Press UEFN Ducky — the locked-in HOW TO USE guide for this app.",
      async () => {
        await openLlmsSection("skills");
        // If the row isn't mounted yet, fall back so Next can escape.
        if (!getTargetElement("settings.skills.row.ducky")) {
          await wait(400);
        }
      },
    ),
    nextStep(
      "settings.content",
      "HOW TO USE the app",
      "This built-in pack covers setup, IDE hookup, Skills studio, and chats. Keep it enabled.",
      async () => {
        await openLlmsSection("skills");
        await wait(250);
      },
    ),
    clickStep(
      "settings.skills.back",
      "Back",
      "Press Back to return to the skill pack list.",
      async () => {
        await openLlmsSection("skills");
      },
    ),
    clickStep(
      "settings.llms.section.mcps",
      "MCPs",
      "Press MCPs to open servers and plugins.",
      async () => {
        await openLlmsSection("mcps");
      },
    ),
    nextStep(
      "settings.mcp.list",
      "MCP servers",
      "Toggle servers here. Add custom ones with Add server.",
      async () => {
        await openLlmsSection("mcps");
      },
    ),
    nextStep(
      "settings.mcp.add",
      "Add server",
      "Add a custom MCP server, then Apply from an IDE provider under LLMs to ship config to your IDE.",
      async () => {
        await openLlmsSection("mcps");
      },
    ),
    clickStep(
      "settings.llms.section.memory",
      "Memory",
      "Press Memory.",
      async () => {
        await openLlmsSection("memory");
      },
    ),
    nextStep("settings.content", "Memory", "Memory will be managed here.", async () => {
      await openLlmsSection("memory");
    }),
  ];
}

function settingsChromeTabSteps(
  label: "Appearance" | "Audio",
  tabBody: string,
  contentBody: string,
): WalkthroughStep[] {
  return [
    clickStep(settingsTabTargetId(label), label, tabBody, async () => {
      requestOpenSettings();
      await wait(200);
    }),
    nextStep("settings.content", `${label} panel`, contentBody, () => openTab(label)),
  ];
}

export const APP_SHELL_TOUR: WalkthroughDef = {
  id: "app.shell",
  title: "Welcome",
  description: "Top bar, docks, chat history, and opening Settings.",
  autoStart: "first_incomplete",
  onCompleteStart: "settings.store",
  steps: [
    {
      target: "shell.header",
      title: "Top bar",
      body: "Project picker, connection status, layout toggles, and quick open live up here.",
      advance: "next",
      mode: "rect",
    },
    {
      target: "shell.left",
      title: "Left side",
      body: "Your workspace dock — chats, project files, and plugin panels you pin here.",
      advance: "next",
      mode: "rect",
    },
    {
      target: "shell.chat_history",
      title: "Chat history",
      body: "Conversation list and Duckies. Open a chat or create a new one from here.",
      advance: "next",
      mode: "rect",
    },
    {
      target: "shell.main",
      title: "Main area",
      body: "Editors, chat panes, plans, and plugin tabs open in the center.",
      advance: "next",
      mode: "rect",
    },
    {
      target: "shell.right",
      title: "Right side",
      body: "Outline, file history, tester, and other dock panels when pinned on the right.",
      advance: "next",
      mode: "rect",
    },
    {
      target: "header.settings",
      title: "Open Settings",
      body: "Press the Ducky / Settings button to open Settings — required to continue.",
      advance: "require_click",
      mode: "circle",
    },
  ],
};

export const CHAT_COMPOSER_TOUR: WalkthroughDef = {
  id: "chat.composer",
  title: "Chat",
  description: "Composer, mode, usage, model, changes, snip, mic, live voice, and send.",
  autoStart: "never",
  steps: [
    nextStep(
      "chat.composer",
      "Chat box",
      "Type here and send. The toolbar under the box is the rest of this tour.",
      async () => {
        requestShowChatComposer();
        await wait(400);
      },
    ),
    clickStep("chat.composer.input", "Message", "Click the text box — this is where you type to the ducky."),
    clickStep("chat.composer.mode", "Mode", "Click Mode — Ask answers questions, Plan outlines work, Agent edits the island."),
    clickStep("chat.composer.usage", "Usage", "Click the ring — context used, files in session, and a reset live here."),
    clickStep("chat.composer.model", "Model", "Click the picker — choose a model or a coding agent for this chat."),
    clickStep("chat.composer.changes", "Ledger", "Click Ledger — every file this chat wrote, with revert per turn."),
    clickStep("chat.composer.snip", "Snip", "Click Snip — capture a screen region and drop it into the chat."),
    clickStep("chat.composer.mic", "Mic", "Click the mic — dictate instead of typing."),
    clickStep("chat.composer.live", "Live chat", "Click Live — talk back and forth with spoken replies."),
    clickStep("chat.composer.send", "Enter", "Click Send (or press Enter) to run the prompt."),
  ],
};

export const SETTINGS_GENERAL_TOUR: WalkthroughDef = {
  id: "settings.general",
  title: "General",
  description: "App info, project files, Add to UEFN, and logs.",
  autoStart: "never",
  steps: settingsGeneralSteps(),
};

export const SETTINGS_DUCKIES_TOUR: WalkthroughDef = {
  id: "settings.duckies",
  title: "Duckies",
  description: "Profiles, skills, MCPs, and when to use each ducky.",
  autoStart: "never",
  steps: settingsDuckiesSteps(),
};

export const SETTINGS_PLANS_TOUR: WalkthroughDef = {
  id: "settings.plans",
  title: "Plans",
  description: "Plan templates and working project plans.",
  autoStart: "never",
  steps: settingsPlansSteps(),
};

export const SETTINGS_LLMS_TOUR: WalkthroughDef = {
  id: "settings.llms",
  title: "LLMs",
  description: "Providers, skill packs, MCP servers, memory, then one enabled gateway.",
  autoStart: "never",
  steps: settingsLlmsOverviewSteps(),
  resolveSteps: () => withEnabledGatewayTours(settingsLlmsOverviewSteps(), { firstOnly: true }),
};

export const SETTINGS_APPEARANCE_TOUR: WalkthroughDef = {
  id: "settings.appearance",
  title: "Appearance",
  description: "Themes, effects, skins, and panel chrome.",
  autoStart: "never",
  steps: settingsChromeTabSteps(
    "Appearance",
    "Press Appearance to open themes.",
    "Themes, effects, skins, and sound hooks for the panel chrome.",
  ),
};

export const SETTINGS_AUDIO_TOUR: WalkthroughDef = {
  id: "settings.audio",
  title: "Audio",
  description: "Spoken replies, microphone, output device, and volume.",
  autoStart: "never",
  steps: settingsChromeTabSteps(
    "Audio",
    "Press Audio to open voice settings.",
    "Spoken replies, microphone, output device, and volume.",
  ),
};

export const SETTINGS_STORE_TOUR: WalkthroughDef = {
  id: "settings.store",
  title: "Store",
  description: "Browse and install plugins, then set up starter gateways.",
  autoStart: "never",
  onCompleteStart: "llms.setup",
  steps: [
    {
      target: "settings.content",
      title: "Ducky Store",
      body: "Browse plugins, themes, and tools. Sign in with your DuckyOS account to install.",
      advance: "next",
      mode: "rect",
      onEnter: async () => {
        requestOpenSettings("Store");
        await wait(300);
      },
    },
    {
      target: "settings.store.catalog",
      title: "Catalog",
      body: "Cards and rows show what you can install. Open a card for details, install, or updates.",
      advance: "next",
      mode: "rect",
    },
    {
      target: "settings.store.catalog",
      title: "Starter gateways",
      body: "First launch only — downloading Anthropic, Cursor, and OpenAI from the Store so you can pick a model.",
      advance: "next",
      mode: "rect",
      onEnter: async () => {
        requestOpenSettings("Store");
        await ensureStarterLlmGateways();
        await wait(400);
      },
    },
    {
      target: settingsTabTargetId("LLMs"),
      title: "Open LLMs",
      body: "Press LLMs in the sidebar. Anthropic, Cursor, and OpenAI are installed — next we set up each one.",
      advance: "require_click",
      mode: "rect",
      onEnter: async () => {
        requestOpenSettings();
        await wait(200);
      },
    },
  ],
};

function llmsSetupIntroSteps(): WalkthroughStep[] {
  return [
    clickStep(
      settingsTabTargetId("LLMs"),
      "Open LLMs",
      "Press LLMs in the sidebar. Each enabled gateway plugin is a row you will click next.",
      async () => {
        setSuppressStarterPluginTours(false);
        markStarterPluginToursCompleted();
        requestOpenSettings();
        await wait(200);
      },
    ),
    nextStep(
      "settings.llms.providers",
      "Providers",
      "Each row is a button. Press it and the page slides open — API key, Test & Save, IDE, and the coding agent live on that slide.",
      async () => {
        await openLlmsSection("llms");
        selectLlmsProvider(null);
        await wait(280);
      },
    ),
  ];
}

/** Append registered plugin gateway tours (click the row, then each fillable section). */
function withEnabledGatewayTours(
  base: WalkthroughStep[],
  opts?: { firstOnly?: boolean },
): WalkthroughStep[] {
  const tours = listEnabledGatewayTours();
  if (!tours.length) {
    if (opts?.firstOnly) return base;
    return [
      ...base,
      nextStep(
        "settings.llms.providers",
        "No gateways enabled",
        "Enable a gateway plugin in the Store, then replay this tour to click a row and fill each section.",
        async () => {
          await openLlmsSection("llms");
          selectLlmsProvider(null);
          await wait(280);
        },
      ),
    ];
  }
  const picked = opts?.firstOnly ? tours.slice(0, 1) : tours;
  return [...base, ...picked.flatMap((t) => t.steps)];
}

export const LLMS_SETUP_TOUR: WalkthroughDef = {
  id: "llms.setup",
  title: "Set up LLM providers",
  description: "Click each enabled gateway and fill its key, Test & Save, IDE, and coding agent.",
  autoStart: "never",
  steps: llmsSetupIntroSteps(),
  resolveSteps: () => withEnabledGatewayTours(llmsSetupIntroSteps()),
};

export function registerBuiltinTours(register: (def: WalkthroughDef) => void): void {
  register(APP_SHELL_TOUR);
  register(CHAT_COMPOSER_TOUR);
  register(SETTINGS_GENERAL_TOUR);
  register(SETTINGS_DUCKIES_TOUR);
  register(SETTINGS_PLANS_TOUR);
  register(SETTINGS_LLMS_TOUR);
  register(SETTINGS_APPEARANCE_TOUR);
  register(SETTINGS_AUDIO_TOUR);
  register(SETTINGS_STORE_TOUR);
  register(LLMS_SETUP_TOUR);
}
