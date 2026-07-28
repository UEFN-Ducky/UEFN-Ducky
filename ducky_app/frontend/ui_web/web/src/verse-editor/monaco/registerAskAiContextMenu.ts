import { MenuId, MenuRegistry } from "monaco-editor/esm/vs/platform/actions/common/actions.js";
import { CommandsRegistry } from "monaco-editor/esm/vs/platform/commands/common/commands.js";
import { EditorContextKeys } from "monaco-editor/esm/vs/editor/common/editorContextKeys.js";
import { ContextKeyExpr } from "monaco-editor/esm/vs/platform/contextkey/common/contextkey.js";

import { getAskAiHandlers } from "../../contexts/askAiHandlersRef";
import { readSelectionPayload } from "../askAi/askAiEditorRef";
import { ASK_AI_NEW_COMMAND_ID, setAskAiMenuIconMap } from "./askAiMenuIcons";

const ASK_AI_SUBMENU = new MenuId("EditorContextAskAi");
const COMMAND_PREFIX = "ducky.ask.ai.";

export type AskAiMenuChat = {
  id: string;
  name: string;
  /** Resolved avatar URL for the Monaco context-menu icon. */
  iconUrl?: string;
};

let parentRegistered = false;
let itemDisposables: Array<{ dispose: () => void }> = [];

function runAskCommand(chatId: string): void {
  const handlers = getAskAiHandlers();
  if (!handlers) return;
  const payload = readSelectionPayload();
  if (!payload) return;
  handlers.onAsk(chatId, payload);
}

function runAskNewCommand(): void {
  const handlers = getAskAiHandlers();
  if (!handlers?.onAskNew) return;
  const payload = readSelectionPayload();
  if (!payload) return;
  handlers.onAskNew(payload);
}

function registerParentMenuItem(): void {
  if (parentRegistered) return;
  parentRegistered = true;
  MenuRegistry.appendMenuItem(MenuId.EditorContext, {
    submenu: ASK_AI_SUBMENU,
    title: "Ask a ducky",
    group: "navigation",
    order: 101,
  });
}

export function registerAskAiContextMenu(): void {
  registerParentMenuItem();
}

export function syncAskAiMenuItems(chats: AskAiMenuChat[]): void {
  registerParentMenuItem();

  for (const d of itemDisposables) d.dispose();
  itemDisposables = [];

  const iconEntries: Array<{ commandId: string; iconUrl: string }> = [
    { commandId: ASK_AI_NEW_COMMAND_ID, iconUrl: "" }, // filled by setAskAiMenuIconMap default
  ];

  // "Send to new one" first — opens create modal, then autofills the composer.
  itemDisposables.push(
    CommandsRegistry.registerCommand(ASK_AI_NEW_COMMAND_ID, () => runAskNewCommand()),
    MenuRegistry.appendMenuItem(ASK_AI_SUBMENU, {
      command: { id: ASK_AI_NEW_COMMAND_ID, title: "Send to new one" },
      when: EditorContextKeys.hasNonEmptySelection,
      group: "navigation",
      order: 0,
    }),
  );

  if (chats.length === 0) {
    const cmdId = `${COMMAND_PREFIX}__empty`;
    itemDisposables.push(
      CommandsRegistry.registerCommand(cmdId, () => undefined),
      MenuRegistry.appendMenuItem(ASK_AI_SUBMENU, {
        command: { id: cmdId, title: "No duckies yet" },
        when: ContextKeyExpr.false(),
        group: "navigation",
        order: 1,
      }),
    );
    setAskAiMenuIconMap(iconEntries);
    return;
  }

  for (let i = 0; i < chats.length; i++) {
    const chat = chats[i]!;
    const cmdId = `${COMMAND_PREFIX}${chat.id}`;
    if (chat.iconUrl) iconEntries.push({ commandId: cmdId, iconUrl: chat.iconUrl });
    itemDisposables.push(
      CommandsRegistry.registerCommand(cmdId, () => runAskCommand(chat.id)),
      MenuRegistry.appendMenuItem(ASK_AI_SUBMENU, {
        command: { id: cmdId, title: chat.name },
        when: EditorContextKeys.hasNonEmptySelection,
        group: "navigation",
        order: i + 1,
      }),
    );
  }

  setAskAiMenuIconMap(iconEntries);
}
