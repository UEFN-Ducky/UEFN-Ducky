import type { RichHeadingLevel } from "../../types/richContent";

interface RichHeadingProps {
  level: RichHeadingLevel;
  children: React.ReactNode;
}

export function RichHeading({ level, children }: RichHeadingProps) {
  const Tag = `h${level}` as keyof JSX.IntrinsicElements;
  return <Tag className={`rich-heading rich-heading--h${level}`}>{children}</Tag>;
}
