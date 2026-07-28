import type { editor, Position } from "monaco-editor";
import { Separator, SubmenuAction } from "monaco-editor/esm/vs/base/common/actions.js";
import type { IAction } from "monaco-editor/esm/vs/base/common/actions.js";
import { ContextMenuController } from "monaco-editor/esm/vs/editor/contrib/contextmenu/browser/contextmenu.js";

import { getVerseLspSession } from "../lsp/verseLspSession";
import { openQuickOpenFromEditor } from "../../components/quick-open/quickOpenGlobal";
import { ASK_AI_SUBMENU_ACTION_ID, decorateAskAiSubmenu } from "./askAiMenuIcons";

/** Context-menu "Command Palette" — rerouted to the unified quick-open instead of Monaco's native palette. */
const QUICK_COMMAND_ID = "editor.action.quickCommand";

/** Editor context-menu commands removed entirely (F2 rename still works). */
const HIDDEN_COMMAND_IDS = new Set(["editor.action.rename"]);

/** Editor context-menu goto/peek commands hidden when disabled or LSP has no result at cursor. */
const GOTO_COMMAND_IDS = new Set([
  "editor.action.revealDefinition",
  "editor.action.revealDeclaration",
  "editor.action.goToTypeDefinition",
  "editor.action.goToImplementation",
  "editor.action.goToReferences",
  "editor.action.peekDefinition",
  "editor.action.peekDeclaration",
  "editor.action.peekTypeDefinition",
  "editor.action.peekImplementation",
  "editor.action.referenceSearch.trigger",
]);

type GotoAvailability = ReadonlySet<string>;

type ContextMenuControllerProto = {
  _editor: editor.IStandaloneCodeEditor;
  _getMenuActions(model: editor.ITextModel, menuId: unknown): IAction[];
  showContextMenu(anchor?: unknown): void;
  _doShowContextMenu(actions: IAction[], event?: unknown): void;
};

const gotoAvailabilityByController = new WeakMap<ContextMenuControllerProto, GotoAvailability>();

function lspPosition(position: Position) {
  return { line: position.lineNumber - 1, character: position.column - 1 };
}

function hasLspResult(result: unknown): boolean {
  if (!result) return false;
  if (Array.isArray(result)) return result.length > 0;
  return typeof result === "object";
}

async function prefetchGotoAvailability(
  docUri: string,
  position: Position,
): Promise<GotoAvailability> {
  const session = getVerseLspSession();
  if (!session) return new Set();

  const client = session.client;
  const wireUri = client.canonicalUri(docUri);
  if (!client.isDocumentOpen(wireUri)) return new Set();

  const pos = lspPosition(position);
  const [definition, declaration, typeDef, implementation, references] = await Promise.all([
    client.getDefinition(wireUri, pos).catch(() => null),
    client.getDeclaration(wireUri, pos).catch(() => null),
    client.getTypeDefinition(wireUri, pos).catch(() => null),
    client.getImplementation(wireUri, pos).catch(() => null),
    client.getReferences(wireUri, pos).catch(() => null),
  ]);

  const available = new Set<string>();
  if (hasLspResult(definition)) {
    available.add("editor.action.revealDefinition");
    available.add("editor.action.peekDefinition");
  }
  if (hasLspResult(declaration)) {
    available.add("editor.action.revealDeclaration");
    available.add("editor.action.peekDeclaration");
  }
  if (hasLspResult(typeDef)) {
    available.add("editor.action.goToTypeDefinition");
    available.add("editor.action.peekTypeDefinition");
  }
  if (hasLspResult(implementation)) {
    available.add("editor.action.goToImplementation");
    available.add("editor.action.peekImplementation");
  }
  if (hasLspResult(references)) {
    available.add("editor.action.goToReferences");
    available.add("editor.action.referenceSearch.trigger");
  }
  return available;
}

function shouldShowGotoAction(
  action: IAction,
  availability: GotoAvailability | undefined,
): boolean {
  if (!GOTO_COMMAND_IDS.has(action.id)) return true;
  if (action.enabled === false) return false;
  if (!availability) return true;
  return availability.has(action.id);
}

function redirectQuickCommandAction(action: IAction): IAction {
  return {
    id: action.id,
    label: action.label,
    tooltip: action.tooltip,
    class: action.class,
    enabled: action.enabled,
    checked: action.checked,
    run: () => openQuickOpenFromEditor({ mode: "command", query: ">" }),
  };
}

function filterContextMenuActions(
  actions: IAction[],
  availability: GotoAvailability | undefined,
): IAction[] {
  const out: IAction[] = [];
  for (const action of actions) {
    if (action instanceof Separator) {
      if (out.length > 0 && !(out[out.length - 1] instanceof Separator)) {
        out.push(action);
      }
      continue;
    }

    if (action instanceof SubmenuAction) {
      const filtered = filterContextMenuActions(action.actions, availability);
      if (filtered.length === 0) continue;
      const next = new SubmenuAction(action.id, action.label, filtered, action.class, action.enabled);
      out.push(action.id === ASK_AI_SUBMENU_ACTION_ID ? decorateAskAiSubmenu(next) : next);
      continue;
    }

    if (action.id === QUICK_COMMAND_ID) {
      out.push(redirectQuickCommandAction(action));
      continue;
    }

    if (HIDDEN_COMMAND_IDS.has(action.id)) continue;

    if (!shouldShowGotoAction(action, availability)) continue;
    out.push(action);
  }

  while (out.length > 0 && out[out.length - 1] instanceof Separator) {
    out.pop();
  }
  return out;
}

let patched = false;

/** Hide disabled/unavailable Go to* items and reroute Command Palette to the unified quick-open. */
export function patchEditorContextMenu(): void {
  if (patched) return;
  patched = true;

  const proto = ContextMenuController.prototype as ContextMenuControllerProto;
  const originalGetMenuActions = proto._getMenuActions;
  const originalShowContextMenu = proto.showContextMenu;

  proto._getMenuActions = function getMenuActions(model, menuId) {
    const actions = originalGetMenuActions.call(this, model, menuId);
    return filterContextMenuActions(actions, gotoAvailabilityByController.get(this));
  };

  proto.showContextMenu = function showContextMenu(anchor) {
    const model = this._editor.getModel();
    const position = this._editor.getPosition();
    if (!model || model.getLanguageId() !== "verse" || !position) {
      gotoAvailabilityByController.delete(this);
      originalShowContextMenu.call(this, anchor);
      return;
    }

    void prefetchGotoAvailability(model.uri.toString(), position)
      .then((availability) => {
        gotoAvailabilityByController.set(this, availability);
        originalShowContextMenu.call(this, anchor);
      })
      .catch(() => {
        gotoAvailabilityByController.delete(this);
        originalShowContextMenu.call(this, anchor);
      })
      .finally(() => {
        gotoAvailabilityByController.delete(this);
      });
  };
}
