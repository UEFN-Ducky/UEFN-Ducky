import { ErrorsTab } from "./ErrorsTab";
import { LogTab } from "./LogTab";

export type LogErrorsSectionTab = "log" | "errors";

interface LogErrorsTabProps {
  sectionTab: LogErrorsSectionTab;
}

export function LogErrorsTab({ sectionTab }: LogErrorsTabProps) {
  return (
    <div className="log-errors-tab-shell">
      {sectionTab === "log" ? <LogTab /> : <ErrorsTab />}
    </div>
  );
}
