/**
 * Stand-in for `monaco-editor` under vitest.
 *
 * Monaco ships browser-only entry points and `?worker` imports that Vite cannot
 * resolve in a test environment, so any test whose import graph reaches the
 * Verse editor failed to collect at all — even when the test itself had nothing
 * to do with the editor. `vitest.config.ts` aliases every `monaco-editor`
 * specifier here instead.
 *
 * This is a load-time stand-in, not a fake editor. It exists so modules import
 * cleanly; nothing here is meant to be driven. A test that actually needs Monaco
 * behaviour has to run in a browser, not against this.
 */

/** Satisfies `new editorWorker()` and friends in `setupMonaco`. */
export default class MonacoWorkerStub {
  addEventListener(): void {}
  removeEventListener(): void {}
  postMessage(): void {}
  terminate(): void {}
}

/**
 * A stub that tolerates whatever a module does to it at load time: reading a
 * member, calling it, or constructing it. Monaco's internals do all three at
 * module scope (`new MenuId(...)`, `MenuRegistry.appendMenuItem(...)`), so a
 * plain object is not enough to get the import through.
 */
// eslint-disable-next-line @typescript-eslint/no-explicit-any
function permissive(name: string): any {
  const members = new Map<string | symbol, unknown>();
  const target = function stub() {} as unknown as Record<string | symbol, unknown>;
  return new Proxy(target, {
    get(_t, prop) {
      if (prop === Symbol.toStringTag) return name;
      if (prop === Symbol.toPrimitive) return () => name;
      if (prop === "then") return undefined; // never look like a promise
      if (prop === "name") return name;
      if (!members.has(prop)) members.set(prop, permissive(`${name}.${String(prop)}`));
      return members.get(prop);
    },
    set(_t, prop, value) {
      members.set(prop, value);
      return true;
    },
    has() {
      return true;
    },
    apply() {
      return undefined;
    },
    construct() {
      return {};
    },
  });
}

// --- `import * as monaco from "monaco-editor"` -------------------------------------

export const editor = permissive("editor");
export const languages = permissive("languages");
export const Uri = permissive("Uri");
export const Range = permissive("Range");
export const Position = permissive("Position");
export const KeyMod = permissive("KeyMod");
export const KeyCode = permissive("KeyCode");
export const MarkerSeverity = permissive("MarkerSeverity");

// --- Monaco's internal ESM modules, used by the context-menu patches ---------------

export class Separator {}
export class SubmenuAction {
  constructor(
    public id?: string,
    public label?: string,
    public actions?: unknown[],
  ) {}
}
export const ContextMenuController = permissive("ContextMenuController");
export const MenuId = permissive("MenuId");
export const MenuRegistry = permissive("MenuRegistry");
export const CommandsRegistry = permissive("CommandsRegistry");
export const EditorContextKeys = permissive("EditorContextKeys");
export const ContextKeyExpr = permissive("ContextKeyExpr");
