import { Icons } from "../../icons/Icons";
import type { OpenFileHandler } from "../../types/richContent";
import { basename } from "../../verse-editor/utils/isVerseFile";
import { normalizeWorkspacePath } from "./isWorkspacePath";

interface RichFileLinkProps {
  path: string;
  label?: string;
  onOpenFile?: OpenFileHandler;
  line?: number;
}

export function RichFileLink({ path, label, onOpenFile, line }: RichFileLinkProps) {
  const norm = normalizeWorkspacePath(path);
  const display = label || basename(norm);

  if (!onOpenFile) {
    return <span className="rich-file-link rich-file-link--static">{display}</span>;
  }

  return (
    <button
      type="button"
      className="rich-file-link"
      onClick={() => onOpenFile(norm, display, line !== undefined ? { line } : undefined)}
      title={norm}
    >
      <span className="rich-file-link-icon">
        <Icons.File />
      </span>
      <span className="rich-file-link-label">{display}</span>
    </button>
  );
}
