import type { editor } from "monaco-editor";

import { defaultFoundation, type FoundationKey } from "../../theme/defaultTokens";

import { computeCssVars } from "../../theme/tokenEngine";

import tmTheme from "../lang/verse-dark.tmTheme.json";

import { buildThemeFromTmTheme } from "./tmThemeToMonaco";



const THEME_ID = "verse-dark";



const DEFAULT_FOUNDATION = defaultFoundation();



function defaultAppearanceVars(): Record<string, string> {

  return computeCssVars({

    foundation: DEFAULT_FOUNDATION,

    overrides: {},

    statusOverrides: {},

  });

}



export function buildVerseMonacoTheme(
  vars: Record<string, string>,
  _foundation: Record<FoundationKey, string> = DEFAULT_FOUNDATION,
): editor.IStandaloneThemeData {
  // vars from computeCssVars already includes verse-syntax overrides — do not re-derive.
  return buildThemeFromTmTheme(tmTheme, vars);
}



export function applyVerseMonacoTheme(
  monaco: typeof import("monaco-editor"),
  vars: Record<string, string>,
  foundation: Record<FoundationKey, string> = DEFAULT_FOUNDATION,
  editors?: Array<editor.IStandaloneCodeEditor | null | undefined>,
): void {
  monaco.editor.defineTheme(THEME_ID, buildVerseMonacoTheme(vars, foundation));
  monaco.editor.setTheme(THEME_ID);
  for (const ed of editors ?? []) {
    if (!ed) continue;
    const model = ed.getModel();
    if (model && !model.isDisposed()) {
      const lang = model.getLanguageId();
      monaco.editor.setModelLanguage(model, lang);
    }
  }
}



/** Initial theme before appearance CSS vars are available. */

export function registerVerseMonacoTheme(monaco: typeof import("monaco-editor")): void {

  applyVerseMonacoTheme(monaco, defaultAppearanceVars(), DEFAULT_FOUNDATION);

}


