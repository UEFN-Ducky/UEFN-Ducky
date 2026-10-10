import { formToConfig, profileToForm } from "../ducky/duckyProfileForm";
import { getApi } from "../../hooks/usePanelApi";
import type { AgentProfileDto, FolderItem } from "../../types/panel";
import { chatsInDuckiesTree, pickChatForGlobalAgent } from "../../utils/globalAgents";

/** Open the chat this library agent already has, or create one. Used by Archive. */
export async function openLibraryAgent(
  profile: AgentProfileDto,
  folders: FolderItem[],
  rootChats: FolderItem["chats"],
  projectSlug: string,
): Promise<{ id: string; name: string } | null> {
  const existing = pickChatForGlobalAgent(chatsInDuckiesTree(folders, rootChats), profile, projectSlug);
  if (existing) return existing;
  const api = getApi();
  if (!api?.create_conversation) return null;
  const config = formToConfig(profileToForm(profile), profile.name, profile.id);
  const created = await api.create_conversation("", profile.ducky_style, undefined, config);
  if (!created?.id) return null;
  return { id: created.id, name: created.title || profile.name };
}
