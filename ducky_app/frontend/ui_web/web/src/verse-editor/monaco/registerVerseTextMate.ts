import type { languages } from "monaco-editor";
import { Registry, type IGrammar, type StateStack, INITIAL } from "vscode-textmate";
import { loadWASM, OnigScanner, OnigString } from "vscode-oniguruma";
import onigWasmUrl from "vscode-oniguruma/release/onig.wasm?url";

import verseGrammar from "../lang/verse.tmGrammar.json";
import langConfig from "../lang/language-configuration.json";
import tmTheme from "../lang/verse-dark.tmTheme.json";
import { createScopeMatcher } from "./tmThemeToMonaco";
import { verseEditorLog, verseEditorLogError, verseEditorWarn } from "../verseEditorLog";

const VERSE_SCOPE = "source.verse";

class TokenizerState implements languages.IState {
  constructor(public readonly ruleStack: StateStack) {}

  clone(): TokenizerState {
    return new TokenizerState(this.ruleStack);
  }

  equals(other: languages.IState): boolean {
    return other instanceof TokenizerState && this.ruleStack.equals(other.ruleStack);
  }
}

let grammarPromise: Promise<IGrammar> | null = null;
let tokenizeErrorLogged = false;

/** Allow tokenize errors to log again after a project switch refresh. */
export function resetVerseTextMateTokenizeErrors(): void {
  tokenizeErrorLogged = false;
}

async function loadGrammar(): Promise<IGrammar> {
  if (!grammarPromise) {
    grammarPromise = (async () => {
      verseEditorLog("textmate", "loading onig.wasm", { url: onigWasmUrl });
      const wasmResponse = await fetch(onigWasmUrl);
      if (!wasmResponse.ok) {
        throw new Error(
          `Failed to fetch onig.wasm (${wasmResponse.status} ${wasmResponse.statusText})`,
        );
      }
      await loadWASM(await wasmResponse.arrayBuffer());
      verseEditorLog("textmate", "onig.wasm loaded");

      const registry = new Registry({
        onigLib: Promise.resolve({
          createOnigScanner: (sources: string[]) => new OnigScanner(sources),
          createOnigString: (str: string) => new OnigString(str),
        }),
        loadGrammar: async (scopeName: string) => {
          if (scopeName === VERSE_SCOPE) {
            return verseGrammar as unknown as import("vscode-textmate").IRawGrammar;
          }
          verseEditorWarn("textmate", "unknown grammar scope requested", { scopeName });
          return null;
        },
      });

      const grammar = await registry.loadGrammar(VERSE_SCOPE);
      if (!grammar) {
        throw new Error(`TextMate registry returned null for scope ${VERSE_SCOPE}`);
      }
      verseEditorLog("textmate", "grammar loaded", { scopeName: VERSE_SCOPE });
      return grammar;
    })().catch((err) => {
      grammarPromise = null;
      verseEditorLogError("textmate", "grammar load failed", err);
      throw err;
    });
  }
  return grammarPromise;
}

function applyLanguageConfiguration(monaco: typeof import("monaco-editor")): void {
  const cfg = langConfig as unknown as {
    comments?: { lineComment?: string; blockComment?: [string, string] };
    brackets?: [string, string][];
    indentationRules?: {
      increaseIndentPattern: string;
      decreaseIndentPattern: string;
    };
  };

  let indentationRules: languages.LanguageConfiguration["indentationRules"];
  if (cfg.indentationRules) {
    try {
      indentationRules = {
        increaseIndentPattern: new RegExp(cfg.indentationRules.increaseIndentPattern),
        decreaseIndentPattern: new RegExp(cfg.indentationRules.decreaseIndentPattern),
      };
    } catch (err) {
      verseEditorLogError("textmate", "invalid indentationRules in language-configuration.json", err);
    }
  }

  monaco.languages.setLanguageConfiguration("verse", {
    comments: cfg.comments
      ? {
          lineComment: cfg.comments.lineComment,
          blockComment: cfg.comments.blockComment,
        }
      : { lineComment: "#", blockComment: ["<#", "#>"] },
    brackets: cfg.brackets ?? [
      ["{", "}"],
      ["[", "]"],
      ["(", ")"],
    ],
    autoClosingPairs: [
      { open: "{", close: "}" },
      { open: "[", close: "]" },
      { open: "(", close: ")" },
      { open: '"', close: '"' },
      { open: "<#", close: "#>" },
    ],
    surroundingPairs: [
      { open: "{", close: "}" },
      { open: "[", close: "]" },
      { open: "(", close: ")" },
      { open: '"', close: '"' },
    ],
    indentationRules,
  });
}

export function getVerseTmTheme() {
  return tmTheme;
}

export async function registerVerseTextMate(monaco: typeof import("monaco-editor")): Promise<void> {
  if (monaco.languages.getLanguages().some((l) => l.id === "verse")) {
    const existing = monaco.languages.getLanguages().find((l) => l.id === "verse");
    if (existing?.extensions?.includes(".versetest")) return;
  }

  monaco.languages.register({
    id: "verse",
    extensions: [".verse", ".versetest", ".vson"],
    aliases: ["Verse", "verse"],
  });

  applyLanguageConfiguration(monaco);

  const grammar = await loadGrammar();
  const matchScope = createScopeMatcher(tmTheme);

  monaco.languages.setTokensProvider("verse", {
    getInitialState: () => new TokenizerState(INITIAL),
    tokenize: (line: string, state: languages.IState) => {
      try {
        const stack = state instanceof TokenizerState ? state.ruleStack : INITIAL;
        const result = grammar.tokenizeLine(line, stack);
        const tokens: languages.IToken[] = result.tokens.map((token) => ({
          startIndex: token.startIndex,
          scopes: matchScope(token.scopes),
        }));
        return { tokens, endState: new TokenizerState(result.ruleStack) };
      } catch (err) {
        if (!tokenizeErrorLogged) {
          tokenizeErrorLogged = true;
          verseEditorLogError("textmate", "tokenizeLine failed — check verse.tmGrammar.json", err);
        }
        return {
          tokens: [{ startIndex: 0, scopes: "invalid" }],
          endState: state,
        };
      }
    },
  });

  verseEditorLog("textmate", "tokens provider registered");
}
