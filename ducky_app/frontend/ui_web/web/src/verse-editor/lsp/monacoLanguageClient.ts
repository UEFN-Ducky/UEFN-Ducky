/**
 * LSP integration uses vscode-languageserver-protocol types + extended Monaco providers.
 * monaco-languageclient v9 requires @codingame/monaco-vscode-api (full VS Code shell);
 * we keep standard monaco-editor and wire the same LSP surface via registerLspProviders.
 */
export { registerVerseLspProviders } from "./registerLspProviders";
