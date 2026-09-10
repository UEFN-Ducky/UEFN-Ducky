/**
 * Keep standard monaco-editor and wire LSP via registerLspProviders.
 * Do not add monaco-languageclient — it pulls the full VS Code shell + vulnerable deps.
 */
export { registerVerseLspProviders } from "./registerLspProviders";
