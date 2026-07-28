declare module "monaco-editor/esm/vs/platform/actions/common/actions.js" {
  export class MenuId {
    constructor(id: string);
    static readonly EditorContext: MenuId;
  }
  export const MenuRegistry: {
    appendMenuItem(
      id: MenuId,
      item: Record<string, unknown>,
    ): { dispose: () => void };
  };
}

declare module "monaco-editor/esm/vs/platform/commands/common/commands.js" {
  export const CommandsRegistry: {
    registerCommand(id: string, handler: (...args: unknown[]) => unknown): { dispose: () => void };
  };
}

declare module "monaco-editor/esm/vs/editor/common/editorContextKeys.js" {
  export const EditorContextKeys: {
    hasNonEmptySelection: unknown;
  };
}

declare module "monaco-editor/esm/vs/platform/contextkey/common/contextkey.js" {
  export const ContextKeyExpr: {
    false(): unknown;
  };
}

declare module "monaco-editor/esm/vs/base/common/actions.js" {
  export interface IAction {
    readonly id: string;
    readonly label: string;
    readonly enabled: boolean;
    readonly class?: string;
    readonly tooltip?: string;
    readonly checked?: boolean;
    run?(...args: unknown[]): unknown;
  }
  export class Separator implements IAction {
    readonly id: string;
    readonly label: string;
    readonly enabled: boolean;
  }
  export class SubmenuAction implements IAction {
    readonly id: string;
    readonly label: string;
    readonly enabled: boolean;
    readonly class?: string;
    readonly actions: IAction[];
    constructor(
      id: string,
      label: string,
      actions: IAction[],
      cssClass?: string,
      enabled?: boolean,
    );
  }
}

declare module "monaco-editor/esm/vs/editor/contrib/contextmenu/browser/contextmenu.js" {
  import type { editor } from "monaco-editor";
  import type { IAction } from "monaco-editor/esm/vs/base/common/actions.js";

  export class ContextMenuController {
  static readonly ID: string;
    static get(editor: editor.ICodeEditor): ContextMenuController | null;
    _editor: editor.IStandaloneCodeEditor;
    _getMenuActions(model: editor.ITextModel, menuId: unknown): IAction[];
    showContextMenu(anchor?: unknown): void;
    _doShowContextMenu(actions: IAction[], event?: unknown): void;
  }
}
