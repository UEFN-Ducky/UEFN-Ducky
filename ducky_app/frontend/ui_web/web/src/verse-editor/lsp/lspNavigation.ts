import { basename } from "../utils/isVerseFile";
import type { RevealRequest } from "./registerLspProviders";

export type NavigateToFile = (req: RevealRequest, options?: { activate?: boolean }) => void;

export function revealRequestName(req: RevealRequest): string {
  return basename(req.path);
}
