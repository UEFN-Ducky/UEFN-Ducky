import { Icons } from "../../icons/Icons";

interface RichReportHeaderProps {
  title: string;
  command?: string;
}

export function RichReportHeader({ title, command }: RichReportHeaderProps) {
  return (
    <div className="rich-report-header">
      <h1 className="rich-heading rich-heading--h1 rich-report-header-title">{title}</h1>
      {command ? (
        <span className="rich-report-header-cmd">
          <Icons.Terminal />
          {command}
        </span>
      ) : null}
    </div>
  );
}
