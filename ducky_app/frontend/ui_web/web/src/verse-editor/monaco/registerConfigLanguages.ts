import type * as MonacoNs from "monaco-editor";

type Monaco = typeof MonacoNs;

const VAR_REF = /\$\{[^}]*\}|\$[A-Za-z_]\w*/;

/**
 * `.env` files: `KEY=value` lines, `#` comments (also after a value, past a space),
 * single/double-quoted values (double quotes may span lines and hold escapes),
 * `${VAR}` / `$VAR` references and an optional leading `export`.
 * Token names reuse the editor theme's classes (comment, type.identifier, operator,
 * string, type), so the colours follow the Appearance settings like every other file.
 */
export const DOTENV_LANGUAGE: MonacoNs.languages.IMonarchLanguage = {
  defaultToken: "",
  tokenPostfix: ".dotenv",
  tokenizer: {
    root: [
      [/^\s*#.*$/, "comment"],
      [/^\s*export(?=\s)/, "keyword"],
      [/[A-Za-z_][\w.-]*(?=\s*=)/, "type.identifier"],
      [/=/, "operator", "@value"],
      [/\s+/, ""],
      [/[^\s=]+/, ""],
    ],
    // Everything after `=` up to the end of the line.
    value: [
      [/^/, "", "@pop"],
      [/\s+#.*$/, "comment"],
      [/"/, "string", "@dq"],
      [/'/, "string", "@sq"],
      [VAR_REF, "type"],
      [/[^\s"'$]+|\s+|\$/, "string"],
    ],
    dq: [
      [/[^"\\$]+/, "string"],
      [/\\./, "string.escape"],
      [VAR_REF, "type"],
      [/\$/, "string"],
      [/"/, "string", "@pop"],
    ],
    sq: [
      [/[^']+/, "string"],
      [/'/, "string", "@pop"],
    ],
  },
};

/** `.gitignore`-style pattern files: `#` comments, `!` negation, glob characters. */
export const IGNORE_LANGUAGE: MonacoNs.languages.IMonarchLanguage = {
  defaultToken: "",
  tokenPostfix: ".ignore",
  tokenizer: {
    root: [
      [/^\s*#.*$/, "comment"],
      [/^\s*!/, "operator"],
      [/\\./, "string.escape"],
      [/\*\*|[*?]/, "keyword"],
      [/\[[^\]]*\]/, "keyword"],
      [/\//, "operator"],
      [/[^\\*?[/]+/, "string"],
    ],
  },
};

let registered = false;

/** Register the small config-file languages Monaco doesn't ship (idempotent). */
export function registerConfigLanguages(monaco: Monaco): void {
  if (registered) return;
  registered = true;
  monaco.languages.register({ id: "dotenv", aliases: ["Environment", "dotenv"] });
  monaco.languages.setMonarchTokensProvider("dotenv", DOTENV_LANGUAGE);
  monaco.languages.setLanguageConfiguration("dotenv", {
    comments: { lineComment: "#" },
    brackets: [["{", "}"]],
    autoClosingPairs: [
      { open: '"', close: '"' },
      { open: "'", close: "'" },
      { open: "{", close: "}" },
    ],
  });
  monaco.languages.register({ id: "ignore", aliases: ["Ignore", "ignore"] });
  monaco.languages.setMonarchTokensProvider("ignore", IGNORE_LANGUAGE);
  monaco.languages.setLanguageConfiguration("ignore", { comments: { lineComment: "#" } });
}
