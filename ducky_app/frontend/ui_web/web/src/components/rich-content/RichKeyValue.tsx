interface RichKeyValueProps {
  pairs: { key: string; value: string }[];
}

export function RichKeyValue({ pairs }: RichKeyValueProps) {
  if (pairs.length === 0) return null;
  return (
    <dl className="rich-key-value">
      {pairs.map(({ key, value }) => (
        <div key={key} className="rich-key-value-row">
          <dt className="rich-key-value-key">{key}</dt>
          <dd className="rich-key-value-val">{value}</dd>
        </div>
      ))}
    </dl>
  );
}
