interface RichListProps {
  ordered?: boolean;
  items: string[];
}

export function RichList({ ordered, items }: RichListProps) {
  const Tag = ordered ? "ol" : "ul";
  return (
    <Tag className={`rich-list${ordered ? " rich-list--ordered" : ""}`}>
      {items.map((item, i) => (
        <li key={`${i}-${item.slice(0, 24)}`} className="rich-list-item">
          {item}
        </li>
      ))}
    </Tag>
  );
}
