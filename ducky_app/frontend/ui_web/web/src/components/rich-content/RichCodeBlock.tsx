interface RichCodeBlockProps {
  text: string;
  language?: string;
  inline?: boolean;
}

export function RichCodeBlock({ text, language, inline }: RichCodeBlockProps) {
  if (inline) {
    return <code className="rich-code rich-code--inline">{text}</code>;
  }
  return (
    <pre className="rich-code rich-code--block">
      <code className={language ? `language-${language}` : undefined}>{text}</code>
    </pre>
  );
}
