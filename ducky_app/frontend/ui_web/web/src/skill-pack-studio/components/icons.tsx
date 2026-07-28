const PATHS = {
  folder: "M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z",
  layers: "M12 2L2 7l10 5 10-5-10-5zM2 17l10 5 10-5M2 12l10 5 10-5",
  chevronDown: "M6 9l6 6 6-6",
  chevronUp: "M18 15l-6-6-6 6",
  plus: "M12 5v14M5 12h14",
  trash: "M3 6h18M19 6v14a2 2 0 01-2 2H7a2 2 0 01-2-2V6m3 0V4a2 2 0 012-2h4a2 2 0 012 2v2",
  arrowLeft: "M19 12H5M12 19l-7-7 7-7",
  code: "M16 18l6-6-6-6M8 6l-6 6 6 6",
  target: "M12 22c5.523 0 10-4.477 10-10S17.523 2 12 2 2 6.477 2 12s4.477 10 10 10zM12 16a4 4 0 100-8 4 4 0 000 8z",
  download: "M21 15v4a2 2 0 01-2 2H5a2 2 0 01-2-2v-4M7 10l5 5 5-5M12 15V3",
  upload: "M21 15v4a2 2 0 01-2 2H5a2 2 0 01-2-2v-4M17 8l-5-5-5 5M12 3v12",
  fileText: "M14 2H6a2 2 0 00-2 2v16a2 2 0 002 2h12a2 2 0 002-2V8zM14 2v6h6M16 13H8M16 17H8M10 9H8",
  chevronRight: "M9 18l6-6-6-6",
  filter: "M22 3H2l8 9.46V19l4 2v-8.54L22 3z",
};

export { PATHS };

function Icon({ path, className }: { path: string; className?: string }) {
  return (
    <svg
      className={className}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden
    >
      <path d={path} />
    </svg>
  );
}

export const Icons = {
  Plus: (p: { className?: string }) => <Icon path={PATHS.plus} className={p.className} />,
  Folder: (p: { className?: string }) => <Icon path={PATHS.folder} className={p.className} />,
  ArrowLeft: (p: { className?: string }) => <Icon path={PATHS.arrowLeft} className={p.className} />,
  Code: (p: { className?: string }) => <Icon path={PATHS.code} className={p.className} />,
  Trash: (p: { className?: string }) => <Icon path={PATHS.trash} className={p.className} />,
  Download: (p: { className?: string }) => <Icon path={PATHS.download} className={p.className} />,
  Upload: (p: { className?: string }) => <Icon path={PATHS.upload} className={p.className} />,
  FileText: (p: { className?: string }) => <Icon path={PATHS.fileText} className={p.className} />,
  ChevronDown: (p: { className?: string }) => <Icon path={PATHS.chevronDown} className={p.className} />,
  ChevronRight: (p: { className?: string }) => <Icon path={PATHS.chevronRight} className={p.className} />,
  Filter: (p: { className?: string }) => <Icon path={PATHS.filter} className={p.className} />,
};
