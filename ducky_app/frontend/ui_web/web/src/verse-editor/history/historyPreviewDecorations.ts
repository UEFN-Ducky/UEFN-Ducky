import type { editor } from "monaco-editor";
import { diffLineHistory, type HistoryDiffSegment } from "./historyLineDiff";

type PreviewSession = {
  decorationIds: string[];
  zoneIds: string[];
};

const sessions = new Map<string, PreviewSession>();

function sessionKey(path: string): string {
  return path.replace(/\\/g, "/").toLowerCase();
}

function clearSession(ed: editor.IStandaloneCodeEditor, key: string): void {
  const session = sessions.get(key);
  if (!session) return;
  if (session.decorationIds.length) ed.deltaDecorations(session.decorationIds, []);
  if (session.zoneIds.length) {
    ed.changeViewZones((accessor) => {
      for (const id of session.zoneIds) accessor.removeZone(id);
    });
  }
  sessions.delete(key);
}

function buildGhostNode(text: string): HTMLElement {
  const dom = document.createElement("div");
  dom.className = "verse-history-preview-ghost";
  dom.textContent = `+ ${text}`;
  return dom;
}

function applySegments(
  ed: editor.IStandaloneCodeEditor,
  segments: HistoryDiffSegment[],
): PreviewSession {
  const model = ed.getModel();
  if (!model) return { decorationIds: [], zoneIds: [] };

  const decorations: editor.IModelDeltaDecoration[] = [];
  const zoneIds: string[] = [];

  for (const seg of segments) {
    if (seg.kind !== "remove") continue;
    decorations.push({
      range: {
        startLineNumber: seg.currentLine,
        startColumn: 1,
        endLineNumber: seg.currentLine,
        endColumn: Math.max(1, model.getLineMaxColumn(seg.currentLine)),
      },
      options: {
        isWholeLine: true,
        className: "verse-history-preview-remove",
        marginClassName: "verse-history-preview-margin-remove",
      },
    });
  }

  // Added lines are view zones (not content widgets) so they reserve real
  // vertical space instead of painting over the next line of code.
  ed.changeViewZones((accessor) => {
    for (const seg of segments) {
      if (seg.kind !== "add") continue;
      const afterLineNumber = seg.afterLine <= 0 ? 0 : Math.min(seg.afterLine, model.getLineCount());
      zoneIds.push(
        accessor.addZone({
          afterLineNumber,
          heightInLines: 1,
          domNode: buildGhostNode(seg.text),
        }),
      );
    }
  });

  const decorationIds = decorations.length ? ed.deltaDecorations([], decorations) : [];
  return { decorationIds, zoneIds };
}

export function applyHistoryPreviewDecorations(
  path: string,
  ed: editor.IStandaloneCodeEditor,
  currentText: string,
  historicalText: string,
): number {
  const key = sessionKey(path);
  clearSession(ed, key);
  const segments = diffLineHistory(currentText, historicalText);
  const session = applySegments(ed, segments);
  sessions.set(key, session);
  return segments.filter((s) => s.kind !== "same").length;
}

export function clearHistoryPreviewDecorations(
  path: string,
  ed: editor.IStandaloneCodeEditor | null | undefined,
): void {
  const key = sessionKey(path);
  if (!ed) {
    sessions.delete(key);
    return;
  }
  clearSession(ed, key);
}

export function revealFirstHistoryChange(
  ed: editor.IStandaloneCodeEditor,
  currentText: string,
  historicalText: string,
): void {
  const segments = diffLineHistory(currentText, historicalText);
  const first = segments.find((s) => s.kind !== "same");
  if (!first) return;
  const line = first.kind === "add" ? Math.max(1, first.afterLine) : first.currentLine;
  ed.revealLineInCenter(line);
}
