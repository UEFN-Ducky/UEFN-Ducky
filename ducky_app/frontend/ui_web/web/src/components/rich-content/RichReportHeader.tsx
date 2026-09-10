import { Icons } from "../../icons/Icons";
import type { OpenFileHandler } from "../../types/richContent";
import { RichInline } from "./RichInline";

interface RichReportHeaderProps {
  title: string;
  command?: string;
  onOpenFile?: OpenFileHandler;
}

export function RichReportHeader({ title, command, onOpenFile }: RichReportHeaderProps) {
  return (
    <div className="rich-report-header">
      <h1 className="rich-heading rich-heading--h1 rich-report-header-title"><RichInline text={title} onOpenFile={onOpenFile} /></h1>
      {command ? (
        <span className="rich-report-header-cmd">
          <Icons.Terminal />
          {command}
        </span>
      ) : null}
    </div>
  );
}
