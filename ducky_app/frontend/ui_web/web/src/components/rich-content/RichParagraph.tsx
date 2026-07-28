interface RichParagraphProps {
  children: React.ReactNode;
}

export function RichParagraph({ children }: RichParagraphProps) {
  return <p className="rich-paragraph">{children}</p>;
}
