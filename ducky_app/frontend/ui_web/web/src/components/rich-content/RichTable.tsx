interface RichTableProps {
  headers: string[];
  rows: string[][];
}

export function RichTable({ headers, rows }: RichTableProps) {
  if (headers.length === 0 && rows.length === 0) return null;
  return (
    <div className="rich-table-wrap">
      <table className="rich-table">
        {headers.length > 0 ? (
          <thead>
            <tr>
              {headers.map((h) => (
                <th key={h} className="rich-table-th">
                  {h}
                </th>
              ))}
            </tr>
          </thead>
        ) : null}
        <tbody>
          {rows.map((row, ri) => (
            <tr key={`row-${ri}`}>
              {row.map((cell, ci) => (
                <td key={`cell-${ri}-${ci}`} className="rich-table-td">
                  {cell}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
