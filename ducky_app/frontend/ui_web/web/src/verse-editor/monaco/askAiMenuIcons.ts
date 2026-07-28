import type { IAction } from "monaco-editor/esm/vs/base/common/actions.js";
import { SubmenuAction } from "monaco-editor/esm/vs/base/common/actions.js";

import { CONNECTION_ICONS } from "../../connectionIcons";

export const ASK_AI_SUBMENU_ACTION_ID = "submenuitem.EditorContextAskAi";
export const ASK_AI_NEW_COMMAND_ID = "ducky.ask.ai.__new__";

const ICON_CLASS = "ducky-ask-menu-icon";
const PARENT_ICON_CLASS = "ducky-ask-menu-icon--parent";
const NEW_ICON_CLASS = "ducky-ask-menu-icon--new";

let styleEl: HTMLStyleElement | null = null;
const iconUrls = new Map<string, string>();

function cssSafeId(id: string): string {
  return id.replace(/[^a-zA-Z0-9_-]/g, "_");
}

function ensureStyleEl(): HTMLStyleElement {
  if (styleEl?.isConnected) return styleEl;
  styleEl = document.createElement("style");
  styleEl.setAttribute("data-ducky-ask-menu-icons", "1");
  document.head.appendChild(styleEl);
  return styleEl;
}

function rebuildStyles(): void {
  const rules: string[] = [
    // Monaco puts the glyph in ::before on the same label that holds the text —
    // swap that glyph for a ducky avatar without hiding the label.
    `.monaco-menu .action-label.icon.${ICON_CLASS}::before {
      content: "" !important;
      display: inline-block !important;
      width: 16px;
      height: 16px;
      margin-right: 6px;
      background-size: 16px 16px;
      background-repeat: no-repeat;
      background-position: center;
      vertical-align: middle;
      border-radius: 2px;
      font-size: 0 !important;
    }`,
    `.monaco-menu .action-label.icon.${PARENT_ICON_CLASS}::before,
     .monaco-menu .action-label.icon.${NEW_ICON_CLASS}::before {
      background-image: url(${JSON.stringify(CONNECTION_ICONS.online)});
    }`,
  ];

  for (const [commandId, url] of iconUrls) {
    if (!url) continue;
    const safe = cssSafeId(commandId);
    rules.push(
      `.monaco-menu .action-label.icon.${ICON_CLASS}--${safe}::before { background-image: url(${JSON.stringify(url)}); }`,
    );
  }

  ensureStyleEl().textContent = rules.join("\n");
}

export function setAskAiMenuIconMap(entries: Array<{ commandId: string; iconUrl: string }>): void {
  iconUrls.clear();
  for (const entry of entries) {
    if (entry.iconUrl) iconUrls.set(entry.commandId, entry.iconUrl);
  }
  // Always style the "Send to new one" + parent row with the header online duck.
  iconUrls.set(ASK_AI_NEW_COMMAND_ID, CONNECTION_ICONS.online);
  rebuildStyles();
}

function decorateLeaf(action: IAction): IAction {
  if (action.id === ASK_AI_NEW_COMMAND_ID) {
    return {
      id: action.id,
      label: action.label,
      tooltip: action.tooltip,
      enabled: action.enabled,
      checked: action.checked,
      class: `${ICON_CLASS} ${NEW_ICON_CLASS}`,
      run: (...args: unknown[]) => action.run?.(...args),
    };
  }

  const url = iconUrls.get(action.id);
  if (!url) return action;
  const safe = cssSafeId(action.id);
  return {
    id: action.id,
    label: action.label,
    tooltip: action.tooltip,
    enabled: action.enabled,
    checked: action.checked,
    class: `${ICON_CLASS} ${ICON_CLASS}--${safe}`,
    run: (...args: unknown[]) => action.run?.(...args),
  };
}

/** Attach ducky avatars (and header-duck for "Send to new one") onto Ask a ducky menu rows. */
export function decorateAskAiSubmenu(action: SubmenuAction): SubmenuAction {
  const decorated = action.actions.map(decorateLeaf);
  return new SubmenuAction(
    action.id,
    action.label,
    decorated,
    `submenu ${ICON_CLASS} ${PARENT_ICON_CLASS}`,
    action.enabled,
  );
}
