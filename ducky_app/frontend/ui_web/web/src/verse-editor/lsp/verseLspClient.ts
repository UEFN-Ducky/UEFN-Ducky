import { verseLspLog, verseLspLogError, verseLspWarn } from "./verseLspDebug";
import { toLspProtocolUri, toMonacoProtocolUriFromAbs, fileUrisMatch } from "./uriUtils";
import {
  classifyLspMessage,
  handleServerRequestResult,
  type LspJsonRpcMessage,
} from "./verseLspMessageRouter";

export type LspWorkspaceFolder = { name: string; path: string };

export type LspConnectOptions = {
  projectRoot: string;
  workspaceFolders?: LspWorkspaceFolder[];
  watchFiles?: string[];
  monaco: typeof import("monaco-editor");
};

export type LspDiagnostic = {
  range: {
    start: { line: number; character: number };
    end: { line: number; character: number };
  };
  severity?: number;
  message: string;
};

export type LspPosition = { line: number; character: number };

/** JSON-RPC error *response* — the server is alive and answered, as opposed to a
 * timeout/socket failure where it did not. */
export class LspResponseError extends Error {
  constructor(
    message: string,
    readonly code?: number,
  ) {
    super(message);
    this.name = "LspResponseError";
  }
}

export {
  toFileUri,
  toAbsolutePath,
  toLspProtocolUri,
  toMonacoProtocolUriFromAbs,
  toLspWireUri,
  fileUrisEqual,
  fileUrisMatch,
  fileUriToRelativePath,
  normalizeFileUri,
} from "./uriUtils";

export class VerseLspClient {
  private ws: WebSocket | null = null;
  private nextId = 1;
  private pending = new Map<number, { resolve: (v: unknown) => void; reject: (e: Error) => void }>();
  private openDocuments = new Map<string, { version: number; pendingContent: string; refs: number }>();
  private activeDocumentUri = "";
  private onDiagnosticsCallback: ((uri: string, diagnostics: LspDiagnostic[]) => void) | null = null;
  private onDisconnectCallback: (() => void) | null = null;
  private debounceTimers = new Map<string, ReturnType<typeof setTimeout>>();
  private monaco: typeof import("monaco-editor") | null = null;
  private watchFilePaths: string[] = [];
  private watchFilesNotified = false;
  private activeRequestByKind = new Map<string, number>();
  private inFlightDiagnostics = new Map<string, Promise<LspDiagnostic[]>>();
  private projectRoot = "";
  private workspaceFoldersForLsp: { uri: string; name: string }[] = [];
  private serverCapabilities: Record<string, unknown> = {};

  isConnected(): boolean {
    return this.ws?.readyState === WebSocket.OPEN;
  }

  /** True if verse-lsp advertised this capability at `initialize`, OR capabilities are
   * unknown (a reconnect that reattached to an already-initialized server answers the
   * second `initialize` with an error, so we get none — assume supported then). Only a
   * KNOWN-absent capability returns false. Used to avoid registering Monaco providers for
   * methods verse-lsp doesn't implement (references/declaration/implementation/
   * typeDefinition): those requests never get answered, hang to their 15–30s timeout, and
   * fill the 16-slot request queue — starving documentSymbol + diagnostics (stale Outline
   * and squiggles that look like "the LSP is entirely broken"). */
  supportsCapability(key: string): boolean {
    if (Object.keys(this.serverCapabilities).length === 0) return true;
    const v = this.serverCapabilities[key];
    return v !== undefined && v !== null && v !== false;
  }

  canonicalUri(uri: string): string {
    return this.canonicalDocUri(uri);
  }

  isDocumentOpen(uri: string): boolean {
    return this.findOpenDocumentUri(uri) !== null;
  }

  /** Match an open LSP document by URI (case-insensitive on Windows). */
  findOpenDocumentUri(uri: string): string | null {
    const wireUri = this.canonicalDocUri(uri);
    if (this.openDocuments.has(wireUri)) return wireUri;
    for (const openUri of this.openDocuments.keys()) {
      if (fileUrisMatch(uri, openUri, this.projectRoot)) return openUri;
    }
    return null;
  }

  onDisconnect(cb: () => void): void {
    this.onDisconnectCallback = cb;
  }

  private toLspUri(absPath: string): string {
    if (this.monaco) {
      return this.monaco.Uri.file(absPath).toString();
    }
    return toMonacoProtocolUriFromAbs(absPath);
  }

  /** Epic code2Protocol — Monaco/VS Code URI string for outbound LSP traffic. */
  private canonicalDocUri(uri: string): string {
    if (!this.projectRoot) return uri;
    return toLspProtocolUri(this.projectRoot, uri, this.monaco);
  }

  /** Epic protocol2Code — align LSP URI with an open doc / Monaco model (realpath parity). */
  private resolveProtocolUri(uri: string): string {
    if (!this.projectRoot) return uri;
    for (const openUri of this.openDocuments.keys()) {
      if (fileUrisMatch(uri, openUri, this.projectRoot)) return openUri;
    }
    if (this.monaco) {
      for (const model of this.monaco.editor.getModels()) {
        const modelUri = model.uri.toString();
        if (fileUrisMatch(uri, modelUri, this.projectRoot)) return modelUri;
      }
    }
    return uri;
  }

  private notifyWatchFiles(): void {
    if (this.watchFilesNotified || this.watchFilePaths.length === 0) return;
    const changes = this.watchFilePaths.map((path) => ({
      uri: this.toLspUri(path),
      type: 1 as const,
    }));
    this.notify("workspace/didChangeWatchedFiles", { changes });
    this.watchFilesNotified = true;
    verseLspLog("client", "notify watch files", { count: changes.length, uris: changes.map((c) => c.uri) });
  }

  async connect(wsUrl: string, options: LspConnectOptions): Promise<void>;
  /** @deprecated Use connect(wsUrl, options) */
  async connect(wsUrl: string, projectRoot: string): Promise<void>;
  async connect(wsUrl: string, optionsOrRoot: LspConnectOptions | string): Promise<void> {
    const options: LspConnectOptions =
      typeof optionsOrRoot === "string"
        ? { projectRoot: optionsOrRoot, monaco: null as unknown as typeof import("monaco-editor") }
        : optionsOrRoot;
    const { projectRoot, workspaceFolders, watchFiles, monaco } = options;
    this.projectRoot = projectRoot;
    this.monaco = monaco;
    this.watchFilePaths = watchFiles ?? [];
    this.watchFilesNotified = false;

    const folders =
      workspaceFolders && workspaceFolders.length > 0
        ? workspaceFolders.map((f) => ({
            uri: this.toLspUri(f.path),
            name: f.name,
          }))
        : [
            {
              uri: this.toLspUri(projectRoot),
              name: projectRoot.split(/[/\\]/).pop() || "project",
            },
          ];
    const rootUri = folders[0]?.uri ?? this.toLspUri(projectRoot);
    this.workspaceFoldersForLsp = folders;

    const connectTimeoutMs = 15_000;
    verseLspLog("client", "connect start", { wsUrl, projectRoot, folderCount: folders.length });
    return new Promise((resolve, reject) => {
      let settled = false;
      const fail = (err: Error) => {
        if (settled) return;
        settled = true;
        clearTimeout(timer);
        verseLspLogError("client", "connect failed", err);
        try {
          ws.close();
        } catch {
          // ignore
        }
        reject(err);
      };
      const ws = new WebSocket(wsUrl);
      this.ws = ws;
      const timer = setTimeout(() => fail(new Error("LSP connection timed out")), connectTimeoutMs);

      ws.onopen = () => {
        verseLspLog("client", "websocket open");
        // Socket is up — stop the connect deadline. From here the initialize request's
        // own (generous) timeout governs: verse-lsp answers initialize only after its
        // first workspace load, which can take minutes when several instances load at
        // once. Failing at 15s caused endless teardown→respawn churn that made the
        // load even slower. VS Code waits for initialize; so do we.
        clearTimeout(timer);
        void (async () => {
          try {
            const rootUriForInit = rootUri;
            verseLspLog("client", "initialize", { rootUri: rootUriForInit, workspaceFolders: folders });
            let initResult: unknown = null;
            try {
              initResult = await this.request("initialize", {
              processId: null,
              rootUri: rootUriForInit,
              capabilities: {
                workspace: {
                  applyEdit: true,
                  workspaceFolders: true,
                  configuration: true,
                },
                textDocument: {
                  synchronization: { dynamicRegistration: false },
                  publishDiagnostics: { relatedInformation: true },
                  completion: {
                    contextSupport: true,
                    completionItem: {
                      snippetSupport: true,
                      commitCharactersSupport: true,
                      documentationFormat: ["markdown", "plaintext"],
                      labelDetailsSupport: true,
                    },
                  },
                  hover: { contentFormat: ["markdown", "plaintext"] },
                  definition: { linkSupport: true },
                  references: {},
                  rename: { prepareSupport: true },
                  documentSymbol: { hierarchicalDocumentSymbolSupport: true },
                  signatureHelp: { signatureInformation: { documentationFormat: ["markdown", "plaintext"] } },
                },
              },
              workspaceFolders: folders,
              // First workspace load can take minutes (more when several verse-lsp
              // instances are cold-loading) — do NOT give up at the default 10s.
              }, 180_000);
            } catch (e) {
              // Reconnecting through the bridge reattaches to an already-initialized
              // verse-lsp process, which answers a second `initialize` with a JSON-RPC
              // error. An error *response* proves the server is alive — proceed with the
              // existing session. Timeouts/socket failures are not LspResponseError and
              // still fail the connect.
              if (!(e instanceof LspResponseError)) throw e;
              verseLspWarn("client", "initialize rejected — reusing already-initialized server", {
                error: e.message,
                code: e.code ?? null,
              });
            }
            const caps = (initResult as { capabilities?: Record<string, unknown> } | null)
              ?.capabilities;
            if (caps && typeof caps === "object") this.serverCapabilities = caps;
            this.notify("initialized", {});
            this.notifyWatchFiles();
            verseLspLog("client", "initialize ok", {
              providers: Object.keys(this.serverCapabilities).filter((k) => k.endsWith("Provider")),
            });
            if (settled) return;
            settled = true;
            clearTimeout(timer);
            verseLspLog("client", "connect ready");
            resolve();
          } catch (e) {
            fail(e instanceof Error ? e : new Error("LSP initialize failed"));
          }
        })();
      };

      ws.onmessage = (ev) => {
        try {
          this.handleMessage(String(ev.data));
        } catch (e) {
          verseLspLogError("client", "handleMessage threw", e);
        }
      };

      ws.onerror = () => fail(new Error("LSP WebSocket error"));
      ws.onclose = (ev) => {
        verseLspWarn("client", "websocket closed", { code: ev.code, reason: ev.reason, wasClean: ev.wasClean });
        this.ws = null;
        if (!settled) {
          fail(new Error("LSP WebSocket closed before ready"));
        }
        this.onDisconnectCallback?.();
      };
    });
  }

  onDiagnostics(cb: (uri: string, diagnostics: LspDiagnostic[]) => void): void {
    this.onDiagnosticsCallback = cb;
  }

  openDocument(uri: string, text: string): void {
    const wireUri = this.canonicalDocUri(uri);
    const existing = this.openDocuments.get(wireUri);
    if (existing) {
      existing.refs += 1;
      this.activeDocumentUri = wireUri;
      if (existing.pendingContent !== text) {
        existing.version += 1;
        existing.pendingContent = text;
        verseLspLog("client", "didChange (reopen)", { uri: wireUri, version: existing.version, chars: text.length });
        this.notify("textDocument/didChange", {
          textDocument: { uri: wireUri, version: existing.version },
          contentChanges: [{ text }],
        });
      }
      return;
    }
    this.activeDocumentUri = wireUri;
    this.openDocuments.set(wireUri, { version: 1, pendingContent: text, refs: 1 });
    verseLspLog("client", "didOpen", { uri: wireUri, chars: text.length, protocol: wireUri });
    this.notify("textDocument/didOpen", {
      textDocument: {
        uri: wireUri,
        languageId: "verse",
        version: 1,
        text,
      },
    });
  }

  /** Tab hidden / editor unmounted — keep document open in verse-lsp for cross-file queries. */
  releaseDocument(uri?: string): void {
    const docUri = this.canonicalDocUri(uri || this.activeDocumentUri);
    if (!docUri) return;
    const doc = this.openDocuments.get(docUri);
    if (!doc) return;
    doc.refs = Math.max(0, doc.refs - 1);
    if (doc.refs > 0) {
      verseLspLog("client", "releaseDocument (retained)", { uri: docUri, refs: doc.refs });
      return;
    }
    this.closeDocument(docUri);
  }

  changeDocument(text: string, uri?: string): void {
    const docUri = this.canonicalDocUri(uri || this.activeDocumentUri);
    if (!docUri) return;
    const doc = this.openDocuments.get(docUri);
    if (!doc) return;
    doc.pendingContent = text;
    const prev = this.debounceTimers.get(docUri);
    if (prev) clearTimeout(prev);
    this.debounceTimers.set(
      docUri,
      setTimeout(() => {
        const current = this.openDocuments.get(docUri);
        if (!current) return;
        current.version += 1;
        verseLspLog("client", "didChange (debounced)", { uri: docUri, version: current.version });
        this.notify("textDocument/didChange", {
          textDocument: { uri: docUri, version: current.version },
          contentChanges: [{ text: current.pendingContent }],
        });
        // 300ms after the LAST keystroke: fast typists don't spam verse-lsp with a
        // didChange per character, a pause still gets squiggles well under a second.
      }, 300),
    );
  }

  flushChanges(uri?: string): void {
    const docUri = this.canonicalDocUri(uri || this.activeDocumentUri);
    if (!docUri) return;
    const timer = this.debounceTimers.get(docUri);
    if (!timer) return;
    clearTimeout(timer);
    this.debounceTimers.delete(docUri);
    const doc = this.openDocuments.get(docUri);
    if (!doc) return;
    doc.version += 1;
    verseLspLog("client", "didChange (flush)", { uri: docUri, version: doc.version });
    this.notify("textDocument/didChange", {
      textDocument: { uri: docUri, version: doc.version },
      contentChanges: [{ text: doc.pendingContent }],
    });
  }

  closeDocument(uri?: string): void {
    const docUri = this.canonicalDocUri(uri || this.activeDocumentUri);
    if (!docUri) return;
    const timer = this.debounceTimers.get(docUri);
    if (timer) {
      clearTimeout(timer);
      this.debounceTimers.delete(docUri);
    }
    if (this.ws?.readyState === WebSocket.OPEN) {
      this.notify("textDocument/didClose", { textDocument: { uri: docUri } });
    }
    this.openDocuments.delete(docUri);
    if (this.activeDocumentUri === docUri) {
      const next = this.openDocuments.keys().next();
      this.activeDocumentUri = next.done ? "" : next.value;
    }
  }

  getDefinition(uri: string, position: LspPosition): Promise<unknown> {
    const wireUri = this.canonicalDocUri(uri);
    if (!this.isDocumentOpen(wireUri)) {
      return Promise.resolve(null);
    }
    this.flushChanges(wireUri);
    return this.request(
      "textDocument/definition",
      {
        textDocument: { uri: wireUri },
        position,
      },
      15_000,
      "definition",
    );
  }

  getCompletion(
    uri: string,
    position: LspPosition,
    context?: { triggerKind: number; triggerCharacter?: string },
  ): Promise<unknown> {
    const wireUri = this.canonicalDocUri(uri);
    this.flushChanges(wireUri);
    return this.request(
      "textDocument/completion",
      {
        textDocument: { uri: wireUri },
        position,
        ...(context ? { context } : {}),
      },
      10_000,
      "completion",
    );
  }

  resolveCompletionItem(item: unknown): Promise<unknown> {
    return this.request("completionItem/resolve", item as object);
  }

  getHover(uri: string, position: LspPosition): Promise<unknown> {
    const wireUri = this.canonicalDocUri(uri);
    if (!this.isDocumentOpen(wireUri)) {
      return Promise.resolve(null);
    }
    this.flushChanges(wireUri);
    return this.request(
      "textDocument/hover",
      {
        textDocument: { uri: wireUri },
        position,
      },
      15_000,
      "hover",
    );
  }

  getReferences(uri: string, position: LspPosition): Promise<unknown> {
    const wireUri = this.canonicalDocUri(uri);
    this.flushChanges(wireUri);
    return this.request(
      "textDocument/references",
      {
        textDocument: { uri: wireUri },
        position,
        context: { includeDeclaration: false },
      },
      // 120s left the UI hanging for two minutes when verse-lsp wedged on a query.
      30_000,
      "references",
    );
  }

  prepareRename(uri: string, position: LspPosition): Promise<unknown> {
    const wireUri = this.canonicalDocUri(uri);
    return this.request("textDocument/prepareRename", {
      textDocument: { uri: wireUri },
      position,
    });
  }

  rename(uri: string, position: LspPosition, newName: string): Promise<unknown> {
    const wireUri = this.canonicalDocUri(uri);
    return this.request("textDocument/rename", {
      textDocument: { uri: wireUri },
      position,
      newName,
    });
  }

  getDocumentSymbols(uri: string): Promise<unknown> {
    const wireUri = this.canonicalDocUri(uri);
    if (!this.isDocumentOpen(wireUri)) {
      return Promise.resolve([]);
    }
    this.flushChanges(wireUri);
    return this.request(
      "textDocument/documentSymbol",
      { textDocument: { uri: wireUri } },
      45_000,
      "documentSymbol",
    );
  }

  async pullDocumentDiagnostics(uri: string): Promise<LspDiagnostic[]> {
    const wireUri = this.canonicalDocUri(uri);
    if (!this.isDocumentOpen(wireUri)) {
      return [];
    }

    // Coalesce concurrent pulls for the same file — a second caller reuses the
    // in-flight request instead of firing a duplicate that cancels the first.
    const existing = this.inFlightDiagnostics.get(wireUri);
    if (existing) return existing;

    const pull = (async (): Promise<LspDiagnostic[]> => {
      this.flushChanges(wireUri);
      try {
        const result = (await this.request(
          "textDocument/diagnostic",
          { textDocument: { uri: wireUri } },
          // Generous: verse-lsp answers nothing until its initial workspace load
          // finishes (can take ~1min on first open) — a 15s timeout just produced
          // timeout storms during boot instead of one slow-but-correct answer.
          45_000,
          // Per-file cancel key — a pull for another file must not cancel this one.
          `diagnostic:${wireUri}`,
        )) as {
          items?: Array<
            (LspDiagnostic & { diagnostics?: never }) | { diagnostics?: LspDiagnostic | LspDiagnostic[] }
          >;
        };
        const flat: LspDiagnostic[] = [];
        if (Array.isArray(result?.items)) {
          for (const item of result.items) {
            if (!item || typeof item !== "object") continue;
            const d = (item as { diagnostics?: LspDiagnostic | LspDiagnostic[] }).diagnostics;
            if (Array.isArray(d)) {
              flat.push(...d);
            } else if (d && typeof d === "object") {
              flat.push(d);
            } else if ("range" in item && "message" in item) {
              // Spec-compliant DocumentDiagnosticReport: items ARE the diagnostics
              // ({kind:"full", items: Diagnostic[]}) — verse-lsp uses this shape, and
              // expecting a nested .diagnostics field silently parsed every pull to [].
              flat.push(item as LspDiagnostic);
            }
          }
        }
        // Non-empty: always publish so errors show. Empty: only publish (which CLEARS markers)
        // when the connection is healthy and the doc is still open — otherwise a pull that
        // resolves empty mid-reset/disconnect would wipe valid squiggles.
        if (flat.length > 0 || (this.isConnected() && this.isDocumentOpen(wireUri))) {
          this.onDiagnosticsCallback?.(wireUri, flat);
        }
        return flat;
      } catch (e) {
        verseLspLogError("client", "pullDocumentDiagnostics failed", e);
        return [];
      } finally {
        this.inFlightDiagnostics.delete(wireUri);
      }
    })();

    this.inFlightDiagnostics.set(wireUri, pull);
    return pull;
  }

  getSignatureHelp(uri: string, position: LspPosition): Promise<unknown> {
    const wireUri = this.canonicalDocUri(uri);
    this.flushChanges(wireUri);
    return this.request("textDocument/signatureHelp", {
      textDocument: { uri: wireUri },
      position,
    });
  }

  getCodeActions(uri: string, range: { start: LspPosition; end: LspPosition }): Promise<unknown> {
    const wireUri = this.canonicalDocUri(uri);
    if (!this.isDocumentOpen(wireUri)) {
      return Promise.resolve([]);
    }
    if (this.pending.size >= 8) {
      return Promise.resolve([]);
    }
    return this.request(
      "textDocument/codeAction",
      {
        textDocument: { uri: wireUri },
        range,
        context: { diagnostics: [] },
      },
      10_000,
      "codeAction",
    );
  }

  getFoldingRanges(uri: string): Promise<unknown> {
    const wireUri = this.canonicalDocUri(uri);
    if (!this.isDocumentOpen(wireUri)) {
      return Promise.resolve([]);
    }
    if (this.pending.size >= 8) {
      return Promise.resolve([]);
    }
    return this.request(
      "textDocument/foldingRange",
      { textDocument: { uri: wireUri } },
      15_000,
      "foldingRange",
    );
  }

  getDeclaration(uri: string, position: LspPosition): Promise<unknown> {
    const wireUri = this.canonicalDocUri(uri);
    if (!this.isDocumentOpen(wireUri)) {
      return Promise.resolve(null);
    }
    this.flushChanges(wireUri);
    return this.request(
      "textDocument/declaration",
      {
        textDocument: { uri: wireUri },
        position,
      },
      15_000,
      "declaration",
    );
  }

  getImplementation(uri: string, position: LspPosition): Promise<unknown> {
    const wireUri = this.canonicalDocUri(uri);
    if (!this.isDocumentOpen(wireUri)) {
      return Promise.resolve(null);
    }
    this.flushChanges(wireUri);
    return this.request(
      "textDocument/implementation",
      {
        textDocument: { uri: wireUri },
        position,
      },
      15_000,
      "implementation",
    );
  }

  getTypeDefinition(uri: string, position: LspPosition): Promise<unknown> {
    const wireUri = this.canonicalDocUri(uri);
    if (!this.isDocumentOpen(wireUri)) {
      return Promise.resolve(null);
    }
    this.flushChanges(wireUri);
    return this.request(
      "textDocument/typeDefinition",
      {
        textDocument: { uri: wireUri },
        position,
      },
      15_000,
      "typeDefinition",
    );
  }

  disconnect(): void {
    verseLspLog("client", "disconnect", { openDocs: [...this.openDocuments.keys()] });
    for (const uri of [...this.openDocuments.keys()]) {
      this.closeDocument(uri);
    }
    for (const timer of this.debounceTimers.values()) {
      clearTimeout(timer);
    }
    this.debounceTimers.clear();
    this.inFlightDiagnostics.clear();
    this.activeRequestByKind.clear();
    if (this.ws) {
      this.ws.close();
      this.ws = null;
    }
    for (const [, pending] of this.pending) {
      pending.reject(new Error("LSP disconnected"));
    }
    this.pending.clear();
  }

  private send(msg: object): void {
    if (this.ws?.readyState !== WebSocket.OPEN) {
      verseLspWarn("client", "send skipped — socket not open", msg);
      return;
    }
    this.ws.send(JSON.stringify(msg));
  }

  private cancelRequest(id: number): void {
    const pending = this.pending.get(id);
    if (pending) {
      this.pending.delete(id);
      pending.reject(new Error("LSP request cancelled"));
    }
    if (this.ws?.readyState === WebSocket.OPEN) {
      this.send({ jsonrpc: "2.0", method: "$/cancelRequest", params: { id } });
    }
  }

  private clearActiveRequest(cancelKey: string, id: number): void {
    if (this.activeRequestByKind.get(cancelKey) === id) {
      this.activeRequestByKind.delete(cancelKey);
    }
  }

  private request(method: string, params: object, timeoutMs = 10_000, cancelKey?: string): Promise<unknown> {
    if (this.pending.size >= 16 && !cancelKey) {
      return Promise.reject(new Error("LSP request queue full"));
    }
    if (cancelKey) {
      const prev = this.activeRequestByKind.get(cancelKey);
      if (prev !== undefined) {
        this.cancelRequest(prev);
      }
    }
    const id = this.nextId++;
    if (cancelKey) {
      this.activeRequestByKind.set(cancelKey, id);
    }
    verseLspLog("client", `request → ${method}`, { id, timeoutMs });
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => {
        this.pending.delete(id);
        if (cancelKey) this.clearActiveRequest(cancelKey, id);
        const err = new Error(`LSP request timed out: ${method}`);
        verseLspLogError("client", `request timeout ${method}`, { id, timeoutMs });
        reject(err);
      }, timeoutMs);
      this.pending.set(id, {
        resolve: (v) => {
          clearTimeout(timer);
          if (cancelKey) this.clearActiveRequest(cancelKey, id);
          verseLspLog("client", `request ← ${method}`, { id, hasResult: v !== null && v !== undefined });
          resolve(v);
        },
        reject: (e) => {
          clearTimeout(timer);
          if (cancelKey) this.clearActiveRequest(cancelKey, id);
          if (e.message !== "LSP request cancelled") {
            verseLspLogError("client", `request ✗ ${method}`, e);
          }
          reject(e);
        },
      });
      this.send({ jsonrpc: "2.0", id, method, params });
    });
  }

  private notify(method: string, params: object): void {
    if (method !== "textDocument/didChange") {
      verseLspLog("client", `notify ${method}`);
    }
    this.send({ jsonrpc: "2.0", method, params });
  }

  private respond(id: number, result: unknown): void {
    verseLspLog("client", "server response ←", { id, result });
    this.send({ jsonrpc: "2.0", id, result });
  }

  private respondError(id: number, message: string, code = -32603): void {
    verseLspLogError("client", "server response ✗", { id, message, code });
    this.send({ jsonrpc: "2.0", id, error: { code, message } });
  }

  private handleServerRequest(id: number, method: string, params: unknown): void {
    if (method === "client/registerCapability") {
      const registrations = (params as { registrations?: { method?: string }[] } | undefined)?.registrations;
      if (Array.isArray(registrations)) {
        verseLspLog("client", "registerCapability", {
          methods: registrations.map((r) => r.method).filter(Boolean),
        });
        if (registrations.some((r) => r.method === "workspace/didChangeWatchedFiles")) {
          this.notifyWatchFiles();
        }
      }
    }
    if (method === "workspace/workspaceFolders") {
      verseLspLog("client", "server request → workspace/workspaceFolders", {
        id,
        count: this.workspaceFoldersForLsp.length,
      });
      this.respond(id, this.workspaceFoldersForLsp);
      return;
    }
    verseLspLog("client", `server request → ${method}`, { id, params });
    try {
      const result = handleServerRequestResult(method, params);
      this.respond(id, result);
    } catch (e) {
      const msg = e instanceof Error ? e.message : "server request failed";
      this.respondError(id, msg);
    }
  }

  private handleMessage(data: string): void {
    let msg: LspJsonRpcMessage & {
      params?: { uri?: string; diagnostics?: LspDiagnostic[] };
    };
    try {
      msg = JSON.parse(data) as typeof msg;
    } catch (e) {
      verseLspLogError("client", "JSON parse failed", { preview: data.slice(0, 200), err: e });
      return;
    }

    const kind = classifyLspMessage(msg);

    if (kind === "serverRequest" && msg.method && msg.id !== undefined) {
      this.handleServerRequest(msg.id, msg.method, msg.params);
      return;
    }

    if (kind === "response" && msg.id !== undefined) {
      const pending = this.pending.get(msg.id);
      if (!pending) {
        verseLspWarn("client", "response for unknown id", { id: msg.id });
        return;
      }
      this.pending.delete(msg.id);
      if (msg.error) {
        pending.reject(new LspResponseError(msg.error.message || "LSP request failed", msg.error.code));
      } else {
        pending.resolve(msg.result);
      }
      return;
    }

    if (msg.method === "textDocument/publishDiagnostics" && msg.params?.uri) {
      const diags = msg.params.diagnostics ?? [];
      const uri = this.resolveProtocolUri(msg.params.uri);
      verseLspLog("client", "publishDiagnostics", {
        uri,
        count: diags.length,
        messages: diags.slice(0, 5).map((d) => d.message),
      });
      this.onDiagnosticsCallback?.(uri, diags);
      return;
    }

    if (msg.method) {
      verseLspLog("client", `notification ${msg.method}`, msg.params);
    }
  }
}
