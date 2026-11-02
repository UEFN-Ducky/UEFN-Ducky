import editorWorker from "monaco-editor/editor/editor.worker?worker";
import jsonWorker from "monaco-editor/language/json/json.worker?worker";
import cssWorker from "monaco-editor/language/css/css.worker?worker";
import htmlWorker from "monaco-editor/language/html/html.worker?worker";
import tsWorker from "monaco-editor/language/typescript/ts.worker?worker";
import type * as MonacoNs from "monaco-editor";
import { verseEditorLog, verseEditorLogError } from "../verseEditorLog";
// Static: the editor hosts import it at boot anyway (type-only Monaco, no editor code).
import { registerVerseMonacoTheme } from "./verseTheme";

/**
 * Monaco is loaded on demand, never at boot.
 *
 * Everything here — the editor itself and the register* helpers, which reach
 * into `monaco-editor/esm/...` — is imported inside `setupMonacoOnce`. A single
 * static `import * as monaco` in this file used to pin the whole editor into
 * whichever chunk imported it, and chat code fences import it (RichCodeBlock →
 * LiveCodePreview), so Monaco sat on the first-paint path: 59% of a 5.9 MB boot
 * bundle that most sessions never opened a file to use. The `?worker` imports
 * above stay static; Vite emits those as their own chunks either way.
 *
 * Callers already `await setupMonaco()`, so nothing else had to change.
 */
type Monaco = typeof MonacoNs;

let setupPromise: Promise<Monaco> | null = null;

function configureMonacoWorkers(): void {
  const global = globalThis as typeof globalThis & {
    MonacoEnvironment?: { getWorker: (workerId: string, label: string) => Worker };
  };
  if (global.MonacoEnvironment) return;
  global.MonacoEnvironment = {
    getWorker(_workerId, label) {
      switch (label) {
        case "json":
          return new jsonWorker();
        case "css":
        case "scss":
        case "less":
          return new cssWorker();
        case "html":
        case "handlebars":
        case "razor":
          return new htmlWorker();
        case "typescript":
        case "javascript":
          return new tsWorker();
        default:
          return new editorWorker();
      }
    },
  };
}

async function setupMonacoOnce(): Promise<Monaco> {
  verseEditorLog("setup", "start");
  configureMonacoWorkers();
  // One round trip: the editor and its Verse language setup land together.
  const [monaco, textMate, snippets, formatter, askAi, contextMenu, configLanguages] = await Promise.all([
    import("monaco-editor"),
    import("./registerVerseTextMate"),
    import("./registerVerseSnippets"),
    import("../format/registerVerseFormatter"),
    import("./registerAskAiContextMenu"),
    import("./patchEditorContextMenu"),
    import("./registerConfigLanguages"),
  ]);
  configLanguages.registerConfigLanguages(monaco);
  await textMate.registerVerseTextMate(monaco);
  snippets.registerVerseSnippets(monaco);
  registerVerseMonacoTheme(monaco);
  formatter.registerVerseFormatter(monaco);
  askAi.registerAskAiContextMenu();
  contextMenu.patchEditorContextMenu();
  // Generic expose so Store shell.boot plugins can call monaco.editor.getEditors().
  (globalThis as typeof globalThis & { __duckyMonaco?: Monaco }).__duckyMonaco = monaco;
  verseEditorLog("setup", "complete");
  return monaco;
}

export function setupMonaco(): Promise<Monaco> {
  if (!setupPromise) {
    setupPromise = setupMonacoOnce().catch((err) => {
      setupPromise = null;
      verseEditorLogError("setup", "Monaco / Verse language setup failed", err);
      throw err;
    });
  }
  return setupPromise;
}
