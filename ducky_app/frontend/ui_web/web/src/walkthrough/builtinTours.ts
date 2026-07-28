import { requestOpenSettings } from "../navigation/openSettingsTab";
import { getTargetElement, settingsTabTargetId } from "../ui-targets/registry";
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

function settingsCoreSteps(): WalkthroughStep[] {
  const steps: WalkthroughStep[] = [];

  // ── General ────────────────────────────────────────────────────────────
  steps.push(
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
    nextStep("settings.content", "Log & Errors", "Panel logs and recent errors — last header tab under General.", async () => {
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
  );

  // ── Duckies ────────────────────────────────────────────────────────────
  steps.push(
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
  );

  // ── Plans ──────────────────────────────────────────────────────────────
  steps.push(
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
  );

  // ── LLMs ───────────────────────────────────────────────────────────────
  steps.push(
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
    nextStep("settings.content", "LLMs", "Default model, installed providers, coding agents, and key status.", async () => {
      await openLlmsSection("llms");
    }),
  );

  // Skills → ducky HOW TO USE → back
  steps.push(
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
  );

  // MCPs
  steps.push(
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
  );

  // Memory — one-liner only
  steps.push(
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
  );

  // ── Appearance / Audio (tab + content only) ─────────────────────────────
  for (const t of [
    {
      label: "Appearance",
      title: "Appearance",
      tabBody: "Press Appearance to open themes.",
      contentBody: "Themes, effects, skins, and sound hooks for the panel chrome.",
    },
    {
      label: "Audio",
      title: "Audio",
      tabBody: "Press Audio to open voice settings.",
      contentBody: "Spoken replies, microphone, output device, and volume.",
    },
  ] as const) {
    steps.push(
      clickStep(settingsTabTargetId(t.label), t.title, t.tabBody, async () => {
        requestOpenSettings();
        await wait(200);
      }),
      nextStep(`settings.content`, `${t.title} panel`, t.contentBody, () => openTab(t.label)),
    );
  }

  steps.push(
    clickStep(settingsTabTargetId("Store"), "Store", "Press Store to open the Ducky Store — last stop in Settings.", async () => {
      requestOpenSettings();
      await wait(200);
    }),
  );

  return steps;
}

export const APP_SHELL_TOUR: WalkthroughDef = {
  id: "app.shell",
  title: "Welcome to UEFN Ducky",
  autoStart: "first_incomplete",
  onCompleteStart: "settings.core",
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

export const SETTINGS_CORE_TOUR: WalkthroughDef = {
  id: "settings.core",
  title: "Settings tour",
  autoStart: "never",
  onCompleteStart: "settings.store",
  steps: settingsCoreSteps(),
};

export const SETTINGS_STORE_TOUR: WalkthroughDef = {
  id: "settings.store",
  title: "Store tour",
  autoStart: "never",
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
      target: "settings.store.root",
      title: "Plugins get their own tours",
      body: "When you enable a plugin that has a tutorial, it will walk you through its Settings tab the first time.",
      advance: "next",
      mode: "rect",
    },
  ],
};

export function registerBuiltinTours(register: (def: WalkthroughDef) => void): void {
  register(APP_SHELL_TOUR);
  register(SETTINGS_CORE_TOUR);
  register(SETTINGS_STORE_TOUR);
}
