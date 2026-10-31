import { rememberSettingsSections, requestOpenSettings } from "./openSettingsTab";
import { requestOpenPluginUiTab } from "../plugin-ui/openPluginUiTab";
import { requestPanelItem } from "../plugin-ui/panelOpenItem";
import { parseChatRefHref } from "../components/chatReferences";
import { chatRefFace } from "../components/chatRefFaces";
import { getApi } from "../hooks/usePanelApi";
import { formToConfig, profileToForm } from "../components/ducky/duckyProfileForm";
import { pickAgentChatForProfile } from "../utils/globalAgents";
import { requestFocusGraph } from "../hooks/graphActivity";
import { requestOpenWorkflowsTab } from "./openWorkflowsTab";

type ChatTabOpener = (chat: { id: string; name: string }) => void;

type FileOpener = (path: string, name: string) => void;

let chatOpener: ChatTabOpener | null = null;
let fileOpener: FileOpener | null = null;
let pendingSkill: { packId: string; subId: string } | null = null;
const openingProfiles = new Set<string>();

export function registerOpenChatReference(fn: ChatTabOpener): () => void {
  chatOpener = fn;
  return () => {
    if (chatOpener === fn) chatOpener = null;
  };
}

export function registerOpenProjectFile(fn: FileOpener): () => void {
  fileOpener = fn;
  return () => {
    if (fileOpener === fn) fileOpener = null;
  };
}

export function takePendingSkillPack(): { packId: string; subId: string } | null {
  const next = pendingSkill;
  pendingSkill = null;
  return next;
}

/** A library mention opens that agent's chat, creating one only when none exists. */
async function openProfileAgent(profileId: string, name: string, duckyStyle?: string): Promise<void> {
  if (openingProfiles.has(profileId)) return;
  openingProfiles.add(profileId);
  try {
    const api = getApi();
    if (!api?.list_all_conversations) return;
    const [convs, recents] = await Promise.all([
      api.list_all_conversations(true).catch(() => []),
      api.list_recent_projects().catch(() => []),
    ]);
    const openSlug = recents.find((row) => row.active)?.slug || "";
    const hit = pickAgentChatForProfile(convs, profileId, name, openSlug);
    if (hit) {
      chatOpener?.(hit);
      return;
    }
    const listed = await api.list_agent_profiles?.().catch(() => null);
    const profile = listed?.profiles?.find((row) => row.id === profileId);
    if (!profile || !api.create_conversation) return;
    const config = formToConfig(profileToForm(profile), profile.name, profile.id);
    const created = await api.create_conversation("", profile.ducky_style || duckyStyle, undefined, config);
    if (created?.id) chatOpener?.({ id: created.id, name: created.title || profile.name || name });
  } finally {
    openingProfiles.delete(profileId);
  }
}

function openSettingsSection(section: "skills" | "mcps"): void {
  rememberSettingsSections({ llms: section });
  requestOpenSettings("LLMs");
  queueMicrotask(() => {
    window.dispatchEvent(
      new CustomEvent("ducky:settings-section", { detail: { tab: "LLMs", section } }),
    );
  });
}

/** Open a project file in an editor tab (any path, spaces included). False when nothing can open it yet. */
export function requestOpenProjectFile(path: string): boolean {
  const clean = (path || "").trim().replace(/\\/g, "/");
  if (!clean || !fileOpener) return false;
  fileOpener(clean, clean.split("/").pop() || clean);
  return true;
}

/** Open a chat tab by id. False when nothing can open it yet. */
export function requestOpenChatTab(id: string, name = ""): boolean {
  const clean = (id || "").trim();
  if (!clean || !chatOpener) return false;
  chatOpener({ id: clean, name: name || clean });
  return true;
}

/** Open the tab a composer reference points at. Old messages keep working when the menus are off. */
export function requestOpenChatReference(href: string, label = ""): void {
  const parsed = parseChatRefHref(href);
  if (!parsed) return;
  const name = label.replace(/^[@/]/, "").trim() || parsed.id;
  if (parsed.kind === "workflow" || parsed.kind === "pipeline") {
    requestFocusGraph(parsed.id);
    requestOpenWorkflowsTab();
    return;
  }
  if (parsed.kind === "ducky") {
    chatOpener?.({ id: parsed.id, name });
    return;
  }
  if (parsed.kind === "profile") {
    void openProfileAgent(parsed.id, name, chatRefFace(href)?.duckyStyle);
    return;
  }
  if (parsed.kind === "file") {
    const path = parsed.id;
    fileOpener?.(path, path.split("/").pop() || path);
    return;
  }
  if (parsed.kind === "plugin") {
    const slash = parsed.id.indexOf("/");
    if (slash <= 0) return;
    // plugin:<plugin>/<panel> opens the panel; plugin:<plugin>/<panel>:<item> opens it on that item.
    const pluginId = parsed.id.slice(0, slash);
    const [panelId, ...rest] = parsed.id.slice(slash + 1).split(":");
    const item = rest.join(":");
    if (item) requestPanelItem(pluginId, panelId, item);
    requestOpenPluginUiTab(pluginId, panelId, item ? undefined : name);
    return;
  }
  if (parsed.kind === "mcp") {
    openSettingsSection("mcps");
    return;
  }
  const slash = parsed.kind === "subskill" ? parsed.id.indexOf("/") : -1;
  const packId = slash > 0 ? parsed.id.slice(0, slash) : parsed.id;
  const subId = slash > 0 ? parsed.id.slice(slash + 1) : "";
  pendingSkill = { packId, subId };
  openSettingsSection("skills");
  queueMicrotask(() => {
    window.dispatchEvent(
      new CustomEvent("ducky:open-skill-pack", { detail: { packId, subId } }),
    );
  });
}
