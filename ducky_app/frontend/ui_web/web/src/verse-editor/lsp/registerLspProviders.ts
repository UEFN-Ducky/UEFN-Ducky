import type {
  editor,
  languages,
  Position,
  IRange,
  IMarkdownString,
  CancellationToken,
} from "monaco-editor";
import type { VerseLspClient } from "./verseLspClient";
import { readVerseFile } from "../api/verseEditorApi";
import { basename } from "../utils/isVerseFile";
import { fileUrisMatch, resolveWorkspaceFilePath, toEditorAbsolutePath } from "./uriUtils";
import { ensureLspDocumentOpen, getNavigateToFile } from "./verseLspSession";
import { verseLspLog, verseLspLogError, verseLspWarn } from "./verseLspDebug";
import { lookupDigestDoc, warmDigestDocs } from "./digestDocs";

type LspRange = {
  start: { line: number; character: number };
  end: { line: number; character: number };
};

type LspLocation = {
  uri: string;
  range: LspRange;
};

type LspLocationLink = {
  originSelectionRange?: LspRange;
  targetUri: string;
  targetRange: LspRange;
  targetSelectionRange?: LspRange;
};

type LspMarkupContent = {
  kind: string;
  value: string;
};

type LspHover = {
  contents: string | LspMarkupContent | LspMarkupContent[];
  range?: LspRange;
};

type LspCompletionItem = {
  label: string;
  kind?: number;
  detail?: string;
  documentation?: string | LspMarkupContent;
  insertText?: string;
  insertTextFormat?: number;
  sortText?: string;
  filterText?: string;
  textEdit?: { range: LspRange; newText: string };
};

type LspCompletionList = {
  items?: LspCompletionItem[];
  isIncomplete?: boolean;
};

type ParsedDefinition = {
  uri: string;
  range: IRange;
  originRange?: IRange;
};

export type RevealRequest = {
  path: string;
  line: number;
  column: number;
};

function lspRangeToMonaco(range: LspRange): IRange {
  const startLine = range.start.line + 1;
  const startCol = range.start.character + 1;
  let endLine = range.end.line + 1;
  let endCol = range.end.character + 1;
  if (endLine < startLine || (endLine === startLine && endCol <= startCol)) {
    endCol = startCol + 1;
  }
  return {
    startLineNumber: startLine,
    startColumn: startCol,
    endLineNumber: endLine,
    endColumn: endCol,
  };
}

function lspPosition(position: Position) {
  return {
    line: position.lineNumber - 1,
    character: position.column - 1,
  };
}

function parseDefinitionResult(result: unknown): ParsedDefinition[] {
  if (!result) return [];
  const items = Array.isArray(result) ? result : [result];
  const out: ParsedDefinition[] = [];
  for (const item of items) {
    if (!item || typeof item !== "object") continue;
    const loc = item as LspLocation & LspLocationLink;
    if ("targetUri" in loc && loc.targetUri) {
      out.push({
        uri: loc.targetUri,
        range: lspRangeToMonaco(loc.targetSelectionRange ?? loc.targetRange),
        originRange: loc.originSelectionRange
          ? lspRangeToMonaco(loc.originSelectionRange)
          : undefined,
      });
    } else if ("uri" in loc && loc.uri && loc.range) {
      out.push({ uri: loc.uri, range: lspRangeToMonaco(loc.range) });
    }
  }
  return out;
}

/** Model for a target URI, or null when the target is not accessible from this app
 * (outside the project root — e.g. Verse/Fortnite digest sources — or unreadable).
 * Inaccessible targets are dropped by callers so goto/peek never offer dead entries. */
async function tryEnsureModelForUri(
  monaco: typeof import("monaco-editor"),
  projectRoot: string,
  workspaceFolderPaths: readonly string[],
  uri: string,
): Promise<editor.ITextModel | null> {
  const rel = resolveWorkspaceFilePath(projectRoot, workspaceFolderPaths, uri);
  if (!rel) {
    verseLspWarn("providers", "target outside workspace — dropped", {
      uri,
      projectRoot,
    });
    return null;
  }

  const absPath = toEditorAbsolutePath(projectRoot, rel);
  const monacoUri = monaco.Uri.file(absPath);
  const existing = monaco.editor.getModel(monacoUri);
  if (existing) return existing;

  try {
    const { content, path: canonicalPath } = await readVerseFile(rel);
    const canonicalUri = monaco.Uri.file(toEditorAbsolutePath(projectRoot, canonicalPath));
    const existingCanonical = monaco.editor.getModel(canonicalUri);
    if (existingCanonical) return existingCanonical;
    const model = monaco.editor.createModel(content, "verse", canonicalUri);
    ensureLspDocumentOpen(canonicalUri.toString(), content);
    return model;
  } catch (e) {
    verseLspWarn("providers", "target file not readable — dropped", {
      uri,
      rel,
      error: e instanceof Error ? e.message : String(e),
    });
    return null;
  }
}

async function resolveDefinitionTargets(
  monaco: typeof import("monaco-editor"),
  projectRoot: string,
  workspaceFolderPaths: readonly string[],
  docUri: string,
  model: editor.ITextModel,
  locations: ParsedDefinition[],
): Promise<languages.LocationLink[]> {
  const links: languages.LocationLink[] = [];
  for (const loc of locations) {
    if (
      fileUrisMatch(loc.uri, docUri, projectRoot) ||
      fileUrisMatch(loc.uri, model.uri.toString(), projectRoot)
    ) {
      links.push({
        uri: model.uri,
        range: loc.range,
        originSelectionRange: loc.originRange,
      });
      continue;
    }

    const targetModel = await tryEnsureModelForUri(monaco, projectRoot, workspaceFolderPaths, loc.uri);
    if (!targetModel) continue;
    links.push({
      uri: targetModel.uri,
      range: loc.range,
      originSelectionRange: loc.originRange,
    });
  }
  if (links.length < locations.length) {
    // Per-target drop reasons are warned in tryEnsureModelForUri.
    verseLspWarn("providers", "some targets are not openable and were dropped", {
      requested: locations.length,
      openable: links.length,
    });
  }
  return links;
}

function lspKindToMonaco(
  monaco: typeof import("monaco-editor"),
  kind?: number,
): languages.CompletionItemKind {
  const K = monaco.languages.CompletionItemKind;
  const map: Record<number, languages.CompletionItemKind> = {
    1: K.Text,
    2: K.Method,
    3: K.Function,
    4: K.Constructor,
    5: K.Field,
    6: K.Variable,
    7: K.Class,
    8: K.Interface,
    9: K.Module,
    10: K.Property,
    11: K.Unit,
    12: K.Value,
    13: K.Enum,
    14: K.Keyword,
    15: K.Snippet,
    16: K.Color,
    17: K.File,
    18: K.Reference,
    19: K.Folder,
    20: K.EnumMember,
    21: K.Constant,
    22: K.Struct,
    23: K.Event,
    24: K.Operator,
    25: K.TypeParameter,
  };
  return (kind !== undefined ? map[kind] : undefined) ?? K.Text;
}

function toMarkdownString(doc: string | LspMarkupContent | undefined): IMarkdownString | undefined {
  if (!doc) return undefined;
  if (typeof doc === "string") return { value: doc };
  return { value: doc.value };
}

function parseHover(result: unknown): languages.Hover | null {
  if (!result || typeof result !== "object") return null;
  const hover = result as LspHover;
  let contents: IMarkdownString[] = [];
  if (typeof hover.contents === "string") {
    contents = [{ value: hover.contents }];
  } else if (Array.isArray(hover.contents)) {
    contents = hover.contents.map((c) => ({ value: c.value }));
  } else if (hover.contents?.value) {
    contents = [{ value: hover.contents.value }];
  }
  if (!contents.length) return null;
  return {
    contents,
    range: hover.range ? lspRangeToMonaco(hover.range) : undefined,
  };
}

function parseCompletionItems(
  monaco: typeof import("monaco-editor"),
  model: editor.ITextModel,
  result: unknown,
  position: Position,
): languages.CompletionItem[] {
  if (!result) return [];
  const list = result as LspCompletionList | LspCompletionItem[];
  // Drop empty-label placeholders (Monaco warns "did IGNORE invalid completion item")
  // and digest operator-signature entries (":char =", "[]t + []t") whose labels start
  // with a symbol — they are not insertable identifiers. Kind is NOT a safe filter:
  // verse-lsp marks real members like SubscribeAgent(...) as kind 24/Operator.
  // verse-lsp also duplicates inherited members (Show() arrives twice) — dedupe.
  const seen = new Set<string>();
  const items = (Array.isArray(list) ? list : (list.items ?? [])).filter((item) => {
    if (typeof item?.label !== "string" || !/^[A-Za-z_@]/.test(item.label)) return false;
    const key = `${item.kind}|${item.label}|${item.insertText ?? ""}`;
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });
  // Items without a textEdit must replace the word being typed. A zero-width range at
  // the cursor makes Monaco's per-item filter word empty, which disables prefix
  // filtering (the whole global dump shows on every keystroke) and makes accepting
  // append instead of replace ("bu" + "Abs(...)" → "buAbs(...)").
  const word = model.getWordUntilPosition(position);
  const wordRange = {
    startLineNumber: position.lineNumber,
    endLineNumber: position.lineNumber,
    startColumn: word.startColumn,
    endColumn: position.column,
  };
  return items.map((item) => {
    const range = item.textEdit ? lspRangeToMonaco(item.textEdit.range) : wordRange;
    const out: languages.CompletionItem = {
      label: item.label,
      kind: lspKindToMonaco(monaco, item.kind),
      detail: item.detail,
      documentation: toMarkdownString(item.documentation),
      insertText: item.textEdit?.newText ?? item.insertText ?? item.label,
      sortText: item.sortText,
      filterText: item.filterText,
      range,
    };
    if (item.insertTextFormat === 2) {
      out.insertTextRules = monaco.languages.CompletionItemInsertTextRule.InsertAsSnippet;
    }
    return out;
  });
}

function isCancelled(token: CancellationToken): boolean {
  return token.isCancellationRequested;
}

function isLspCancelled(e: unknown): boolean {
  if (!(e instanceof Error)) return false;
  const msg = e.message.toLowerCase();
  return (
    msg === "lsp request cancelled" ||
    msg === "canceled" ||
    msg.includes("cancelled") ||
    msg.includes("canceled")
  );
}

export function registerVerseLspProviders(
  monaco: typeof import("monaco-editor"),
  client: VerseLspClient,
  projectRoot: string,
  _currentFileUri: string,
  workspaceFolderPaths: readonly string[] = [],
): { dispose(): void } {
  const disposables: Array<{ dispose(): void }> = [];
  verseLspLog("providers", "register verse LSP providers", { projectRoot, workspaceRoots: workspaceFolderPaths.length });
  warmDigestDocs(workspaceFolderPaths);

  // VS Code semantics: providers ALWAYS return locations (with real models) so Peek
  // renders its inline popup. Actual navigation (F12 / goto) flows through the editor
  // opener below, which routes cross-file targets into the app's tab system. The old
  // design navigated directly from the provider and returned null — which made Peek
  // auto-jump instead of showing the popup.
  disposables.push(
    monaco.editor.registerEditorOpener({
      openCodeEditor: (_source, resource, selectionOrPosition) => {
        const rel = resolveWorkspaceFilePath(projectRoot, workspaceFolderPaths, resource.toString());
        if (!rel) {
          verseLspWarn("providers", "editor opener: target outside workspace — not opening", {
            uri: resource.toString(),
            projectRoot,
          });
          return false;
        }
        let line = 1;
        let column = 1;
        if (selectionOrPosition) {
          if ("startLineNumber" in selectionOrPosition) {
            line = selectionOrPosition.startLineNumber;
            column = selectionOrPosition.startColumn;
          } else if ("lineNumber" in selectionOrPosition) {
            line = selectionOrPosition.lineNumber;
            column = selectionOrPosition.column;
          }
        }
        verseLspLog("providers", "editor opener → app tab", { rel, line, column });
        getNavigateToFile()({ path: rel, line, column }, { activate: true });
        return true;
      },
    }),
  );

  disposables.push(
    monaco.languages.registerDefinitionProvider("verse", {
      provideDefinition: async (model, position, token) => {
        if (isCancelled(token)) return null;
        const docUri = model.uri.toString();
        if (!client.isDocumentOpen(docUri)) return null;
        const pos = lspPosition(position);
        verseLspLog("providers", "definition requested (F12/Ctrl+click)", { docUri, pos });
        try {
          const result = await client.getDefinition(docUri, pos);
          if (isCancelled(token)) return null;
          const locations = parseDefinitionResult(result);
          verseLspLog("providers", "definition result", {
            count: locations.length,
            uris: locations.map((l) => l.uri),
          });
          if (!locations.length) return null;
          const links = await resolveDefinitionTargets(
            monaco,
            projectRoot,
            workspaceFolderPaths,
            docUri,
            model,
            locations,
          );
          verseLspLog("providers", "definition targets openable", {
            requested: locations.length,
            openable: links.length,
          });
          return links.length ? links : null;
        } catch (e) {
          if (!isLspCancelled(e)) verseLspLogError("providers", "definition failed", e);
          return null;
        }
      },
    }),
  );

  disposables.push(
    monaco.languages.registerCompletionItemProvider("verse", {
      // Server-declared trigger characters (probed) plus "/" for using-path segments.
      triggerCharacters: [".", ":", "<", ">", "/"],
      provideCompletionItems: async (model, position, context, token) => {
        if (isCancelled(token)) return { suggestions: [] };
        const docUri = model.uri.toString();

        // verse-lsp only produces MEMBER completions when the request position is
        // directly after the "." — one character later ("Foo.S|") it silently falls
        // back to the generic in-scope list (probed 2026-07-05). So when completing
        // mid-word right after a dot, anchor the request at the dot and let Monaco
        // filter the members by the typed prefix via the items' word range.
        const word = model.getWordUntilPosition(position);
        const afterDot =
          word.startColumn > 1 &&
          model.getValueInRange({
            startLineNumber: position.lineNumber,
            startColumn: word.startColumn - 1,
            endLineNumber: position.lineNumber,
            endColumn: word.startColumn,
          }) === ".";
        const lspPos = afterDot
          ? { line: position.lineNumber - 1, character: word.startColumn - 1 }
          : lspPosition(position);
        const lspContext = afterDot
          ? { triggerKind: 2, triggerCharacter: "." }
          : {
              // Monaco CompletionTriggerKind is 0-based (Invoke=0); LSP is 1-based.
              triggerKind: context.triggerKind + 1,
              ...(context.triggerCharacter ? { triggerCharacter: context.triggerCharacter } : {}),
            };

        try {
          const attempt = async (): Promise<languages.CompletionItem[]> => {
            if (!client.isDocumentOpen(docUri)) return [];
            const result = await client.getCompletion(docUri, lspPos, lspContext);
            return parseCompletionItems(monaco, model, result, position);
          };
          let suggestions = await attempt();
          // Pressing "." must pop the member list even if the LSP session was
          // mid-(re)bind or the doc sync just flushed — one short retry covers
          // the transient empty instead of leaving the widget closed.
          if (!suggestions.length && afterDot && !isCancelled(token)) {
            await new Promise((r) => setTimeout(r, 300));
            if (!isCancelled(token)) suggestions = await attempt();
          }
          if (isCancelled(token)) return { suggestions: [] };
          verseLspLog("providers", "completion", { count: suggestions.length, afterDot });
          return { suggestions, incomplete: false };
        } catch (e) {
          if (!isLspCancelled(e)) verseLspLogError("providers", "completion failed", e);
          return { suggestions: [] };
        }
      },
      // verse-lsp has no completionItem/resolve and ships no docs on items — enrich
      // focused items from the parsed digest files instead (lazy, cached).
      resolveCompletionItem: async (item, token) => {
        if (item.documentation || typeof item.label !== "string") return item;
        const entry = await lookupDigestDoc(workspaceFolderPaths, item.label);
        if (!entry || token.isCancellationRequested) return item;
        return {
          ...item,
          detail: entry.signature,
          documentation: {
            value: entry.owner ? `**${entry.owner}**\n\n${entry.doc}` : entry.doc,
          },
        };
      },
    }),
  );

  disposables.push(
    monaco.languages.registerHoverProvider("verse", {
      provideHover: async (model, position, token) => {
        if (isCancelled(token)) return null;
        const docUri = model.uri.toString();
        if (!client.isDocumentOpen(docUri)) return null;
        const pos = lspPosition(position);
        verseLspLog("providers", "hover requested", { docUri, pos });
        try {
          const result = await client.getHover(docUri, pos);
          if (isCancelled(token)) return null;
          const hover = parseHover(result);
          verseLspLog("providers", "hover result", { hasHover: !!hover, result });
          return hover;
        } catch (e) {
          if (!isLspCancelled(e)) verseLspLogError("providers", "hover failed", e);
          return null;
        }
      },
    }),
  );

  disposables.push(
    monaco.languages.registerReferenceProvider("verse", {
      provideReferences: async (model, position, _referenceContext, token) => {
        // verse-lsp doesn't advertise referencesProvider — the request never returns and
        // just burns a queue slot to timeout. Skip it (Monaco's peek shows "no results").
        if (!client.supportsCapability("referencesProvider")) return [];
        if (isCancelled(token)) return [];
        const docUri = model.uri.toString();
        if (!client.isDocumentOpen(docUri)) return [];
        const pos = lspPosition(position);
        verseLspLog("providers", "references requested (Shift+F12)", { docUri, pos });
        try {
          const result = await client.getReferences(docUri, pos);
          if (isCancelled(token)) return [];
          const locations = parseDefinitionResult(result);
          verseLspLog("providers", "references result", { count: locations.length });
          if (!locations.length) return [];

          // Return EVERY openable location with a real model — Monaco's references peek
          // lists them all with previews; clicking one goes through the editor opener.
          // Targets outside the project (digest/SDK files) are dropped.
          const out: languages.Location[] = [];
          for (const loc of locations) {
            if (fileUrisMatch(loc.uri, docUri, projectRoot)) {
              out.push({ uri: model.uri, range: loc.range });
              continue;
            }
            const targetModel = await tryEnsureModelForUri(
              monaco,
              projectRoot,
              workspaceFolderPaths,
              loc.uri,
            );
            if (!targetModel) continue;
            out.push({ uri: targetModel.uri, range: loc.range });
          }
          return out;
        } catch (e) {
          if (!isLspCancelled(e)) verseLspLogError("providers", "references failed", e);
          return [];
        }
      },
    }),
  );

  disposables.push(
    monaco.languages.registerRenameProvider("verse", {
      provideRenameEdits: async (model, position, newName, _token) => {
        const docUri = model.uri.toString();
        try {
          const prepared = await client.prepareRename(docUri, lspPosition(position));
          if (prepared && typeof prepared === "object" && "range" in prepared === false) {
            const err = prepared as { message?: string };
            if (err.message) return { edits: [], rejectReason: err.message };
          }
          const result = (await client.rename(docUri, lspPosition(position), newName)) as {
            changes?: Record<string, Array<{ range: LspRange; newText: string }>>;
          };
          if (!result?.changes) return { edits: [] };
          const edits: languages.IWorkspaceTextEdit[] = [];
          for (const [uri, changes] of Object.entries(result.changes)) {
            for (const change of changes) {
              edits.push({
                resource: monaco.Uri.parse(uri),
                textEdit: {
                  range: lspRangeToMonaco(change.range),
                  text: change.newText,
                },
              } as languages.IWorkspaceTextEdit);
            }
          }
          return { edits };
        } catch (e) {
          verseLspLogError("providers", "rename failed", e);
          return { edits: [] };
        }
      },
    }),
  );

  const locationProvider = async (
    model: editor.ITextModel,
    position: Position,
    token: CancellationToken,
    method: (uri: string, pos: ReturnType<typeof lspPosition>) => Promise<unknown>,
  ): Promise<languages.LocationLink[] | null> => {
    if (isCancelled(token)) return null;
    const docUri = model.uri.toString();
    if (!client.isDocumentOpen(docUri)) return null;
    try {
      const result = await method(docUri, lspPosition(position));
      if (isCancelled(token)) return null;
      const locations = parseDefinitionResult(result);
      if (!locations.length) return null;
      return resolveDefinitionTargets(
        monaco,
        projectRoot,
        workspaceFolderPaths,
        docUri,
        model,
        locations,
      );
    } catch (e) {
      if (!isLspCancelled(e)) verseLspLogError("providers", "location failed", e);
      return null;
    }
  };

  // declaration/implementation/typeDefinition are NOT advertised by verse-lsp — firing them
  // (Monaco prefetches all three when the context menu opens) hangs each request to its 15s
  // timeout and clogs the queue, starving documentSymbol + diagnostics. Return null unless
  // the server actually supports the method.
  disposables.push(
    monaco.languages.registerDeclarationProvider("verse", {
      provideDeclaration: (model, position, token) =>
        client.supportsCapability("declarationProvider")
          ? locationProvider(model, position, token, client.getDeclaration.bind(client))
          : null,
    }),
  );

  disposables.push(
    monaco.languages.registerImplementationProvider("verse", {
      provideImplementation: (model, position, token) =>
        client.supportsCapability("implementationProvider")
          ? locationProvider(model, position, token, client.getImplementation.bind(client))
          : null,
    }),
  );

  disposables.push(
    monaco.languages.registerTypeDefinitionProvider("verse", {
      provideTypeDefinition: (model, position, token) =>
        client.supportsCapability("typeDefinitionProvider")
          ? locationProvider(model, position, token, client.getTypeDefinition.bind(client))
          : null,
    }),
  );

  disposables.push(
    monaco.languages.registerSignatureHelpProvider("verse", {
      signatureHelpTriggerCharacters: ["(", ","],
      provideSignatureHelp: async (model, position, _token) => {
        const docUri = model.uri.toString();
        try {
          const result = (await client.getSignatureHelp(docUri, lspPosition(position))) as {
            signatures?: Array<{
              label: string;
              documentation?: string | LspMarkupContent;
              parameters?: Array<{ label: string | [number, number]; documentation?: string }>;
            }>;
            activeSignature?: number;
            activeParameter?: number;
          };
          if (!result?.signatures?.length) return null;
          return {
            value: {
              signatures: result.signatures.map((sig) => ({
                label: sig.label,
                documentation: toMarkdownString(sig.documentation),
                parameters: (sig.parameters ?? []).map((p) => ({
                  label: p.label,
                  documentation: p.documentation ? { value: p.documentation } : undefined,
                })),
              })),
              activeSignature: result.activeSignature ?? 0,
              activeParameter: result.activeParameter ?? 0,
            },
            dispose: () => {},
          };
        } catch {
          return null;
        }
      },
    }),
  );

  // documentSymbol / codeAction / foldingRange are not registered here — they flood verse-lsp
  // on every keystroke. Outline uses getDocumentSymbols directly (debounced, after didOpen).
  //
  // Document FORMATTING is intentionally NOT wired to verse-lsp: Epic's server ships no
  // formatting capability, so it silently returned nothing. Format Document is handled by
  // the standalone client-side formatter registered once in setupMonaco (see ../format).

  return {
    dispose() {
      for (const d of disposables) d.dispose();
    },
  };
}

export function revealRequestToOpenArgs(req: RevealRequest): { path: string; name: string } {
  return { path: req.path, name: basename(req.path) };
}
