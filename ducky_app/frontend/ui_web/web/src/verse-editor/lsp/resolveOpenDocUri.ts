import {
  fileUrisMatch,
  resolveWorkspaceFilePath,
  toLspProtocolUri,
} from "./uriUtils";
import { getVerseLspSession } from "./verseLspSession";
import { registryKey, registryLookupKeys } from "../utils/isVerseFile";

/** Resolve the LSP wire URI for an open editor document (matches diagnostics refresh logic). */
export function resolveOpenDocUri(
  projectRoot: string,
  relativePath: string,
): { docUri: string; projectRoot: string } | null {
  const session = getVerseLspSession();
  if (!session?.client.isConnected()) return null;

  const relKeys = new Set(registryLookupKeys(relativePath));
  const workspaceFolderPaths = session.workspaceFolderPaths;
  const candidateUri = toLspProtocolUri(session.projectRoot, relativePath, session.monaco);

  for (const model of session.models.values()) {
    if (model.isDisposed()) continue;
    const modelUri = model.uri.toString();
    const rel = resolveWorkspaceFilePath(session.projectRoot, workspaceFolderPaths, modelUri);
    if (rel && relKeys.has(registryKey(rel))) {
      return { docUri: modelUri, projectRoot: session.projectRoot };
    }
    if (fileUrisMatch(modelUri, candidateUri, session.projectRoot)) {
      return { docUri: modelUri, projectRoot: session.projectRoot };
    }
  }

  for (const key of relKeys) {
    const wireUri = session.client.findOpenDocumentUri(
      toLspProtocolUri(session.projectRoot, key, session.monaco),
    );
    if (wireUri) {
      return { docUri: wireUri, projectRoot: session.projectRoot };
    }
  }

  const fallbackUri = session.client.findOpenDocumentUri(
    toLspProtocolUri(projectRoot, relativePath, session.monaco),
  );
  if (fallbackUri) {
    return { docUri: fallbackUri, projectRoot: session.projectRoot };
  }

  return null;
}
