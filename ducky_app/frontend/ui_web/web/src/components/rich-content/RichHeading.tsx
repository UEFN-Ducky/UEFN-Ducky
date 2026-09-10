import type { RichHeadingLevel } from "../../types/richContent";
import { richNodeText, richTextClass } from "./richTextColors";

interface RichHeadingProps {
  level: RichHeadingLevel;
  children: React.ReactNode;
  text?: string;
}

export function RichHeading({ level, children, text }: RichHeadingProps) {
  const Tag = `h${level}` as keyof JSX.IntrinsicElements;
  return <Tag className={`rich-heading rich-heading--h${level} ${richTextClass(text ?? richNodeText(children))}`}>{children}</Tag>;
}
