import type { ReactNode } from "react";
import { richNodeText, richTextClass } from "./richTextColors";

export function RichEmphasis({ children }: { children?: ReactNode }) {
  return <strong className={`rich-emphasis ${richTextClass(richNodeText(children))}`}>{children}</strong>;
}
