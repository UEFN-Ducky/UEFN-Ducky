import { shouldSuppressRemoteChatOpen as suppressDucktactoe } from "./ducktactoeBoardChat";
import { isPluginDockChat } from "./pluginChatDockStore";

export { PluginWebviewPane } from "./PluginWebviewPane";
export type { PluginChatOverlayProps } from "./PluginWebviewPane";
export { markBackgroundMount, registerOpenPluginUiTab, requestOpenPluginUiTab } from "./openPluginUiTab";
export type { OpenPluginUiTabOptions } from "./openPluginUiTab";
export {
  pluginUiTabId,
  pluginUiInstanceTabId,
  parsePluginUiTabId,
  type PluginUiPanel,
} from "./types";
export { orphanedPluginTabs, isOrphanedPluginTab } from "./orphanedPluginTabs";
export { clearBrowserPaneBounds, setBrowserPaneInitialUrl } from "./bridge";
export { PANEL_ACTION_PREFIX } from "./constants";
export {
  clearBoundDucktactoeChat,
  ensureDucktactoeGameChat,
  isDucktactoeChat,
} from "./ducktactoeBoardChat";
export { isPluginDockChat };

/** True when chats_changed should not open a chat as a full tab: the Duck-Tac-Toe board's chat
 * or a chat docked inside a plugin panel (bridge `chat.open`). */
export function shouldSuppressRemoteChatOpen(conv: { id?: string; title?: string }): boolean {
  return suppressDucktactoe(conv) || isPluginDockChat(conv);
}
