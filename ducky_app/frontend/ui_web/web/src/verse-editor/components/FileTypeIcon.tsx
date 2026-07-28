import { Icons } from "../../icons/Icons";
import { isPythonFile, isVerseFile } from "../utils/isVerseFile";
import { PythonIcon } from "./PythonIcon";
import { VerseDiagnosticIcon } from "./VerseDiagnosticIcon";
import { VerseIcon } from "./VerseIcon";

interface FileTypeIconProps {
  path: string;
  size?: number;
  diagnosticErrors?: number;
  diagnosticWarnings?: number;
}

export function FileTypeIcon({
  path,
  size = 14,
  diagnosticErrors = 0,
  diagnosticWarnings = 0,
}: FileTypeIconProps) {
  if (isVerseFile(path)) {
    if (diagnosticErrors > 0 || diagnosticWarnings > 0) {
      return (
        <VerseDiagnosticIcon errors={diagnosticErrors} warnings={diagnosticWarnings} size={size} />
      );
    }
    return <VerseIcon size={size} />;
  }
  if (isPythonFile(path)) {
    return <PythonIcon size={size} />;
  }
  return <Icons.File />;
}
