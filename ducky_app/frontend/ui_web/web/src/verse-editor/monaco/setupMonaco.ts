import editorWorker from "monaco-editor/esm/vs/editor/editor.worker?worker";
import jsonWorker from "monaco-editor/esm/vs/language/json/json.worker?worker";
import cssWorker from "monaco-editor/esm/vs/language/css/css.worker?worker";
import htmlWorker from "monaco-editor/esm/vs/language/html/html.worker?worker";
import tsWorker from "monaco-editor/esm/vs/language/typescript/ts.worker?worker";
import * as monaco from "monaco-editor";
import { registerVerseTextMate } from "./registerVerseTextMate";
import { registerVerseSnippets } from "./registerVerseSnippets";
import { registerVerseMonacoTheme } from "./verseTheme";
import { registerAskAiContextMenu } from "./registerAskAiContextMenu";
import { patchEditorContextMenu } from "./patchEditorContextMenu";
import { registerVerseFormatter } from "../format/registerVerseFormatter";
import { verseEditorLog, verseEditorLogError } from "../verseEditorLog";

let setupPromise: Promise<typeof monaco> | null = null;

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

async function setupMonacoOnce(): Promise<typeof monaco> {
  verseEditorLog("setup", "start");
  configureMonacoWorkers();
  await registerVerseTextMate(monaco);
  registerVerseSnippets(monaco);
  registerVerseMonacoTheme(monaco);
  registerVerseFormatter(monaco);
  registerAskAiContextMenu();
  patchEditorContextMenu();
  // Generic expose so Store shell.boot plugins can call monaco.editor.getEditors().
  (globalThis as typeof globalThis & { __duckyMonaco?: typeof monaco }).__duckyMonaco = monaco;
  verseEditorLog("setup", "complete");
  return monaco;
}

export function setupMonaco(): Promise<typeof monaco> {
  if (!setupPromise) {
    setupPromise = setupMonacoOnce().catch((err) => {
      setupPromise = null;
      verseEditorLogError("setup", "Monaco / Verse language setup failed", err);
      throw err;
    });
  }
  return setupPromise;
}
