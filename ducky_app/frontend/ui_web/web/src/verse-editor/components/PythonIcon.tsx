interface PythonIconProps {
  size?: number;
  className?: string;
  title?: string;
}

/** Python logo — blue/yellow brand colors at any size. */
export function PythonIcon({ size = 13, className = "", title }: PythonIconProps) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 32 32"
      className={className}
      aria-hidden={title ? undefined : true}
      role={title ? "img" : undefined}
      aria-label={title}
    >
      <path
        fill="#3776AB"
        d="M15.885 2c-7.1.3-7.5 3.9-7.5 3.9v3.8h7.5v1.1H6.4S2 10.4 2 17.5c0 7.1 3.9 6.9 3.9 6.9h2.3v-3.3s-.1-3.9 3.8-3.9h6.5s3.7.1 3.7-3.5V6.4S23.2 2.2 15.885 2zm-4.1 2.2a1.1 1.1 0 1 1 0 2.2 1.1 1.1 0 0 1 0-2.2z"
      />
      <path
        fill="#FFD43B"
        d="M16.115 30c7.1-.3 7.5-3.9 7.5-3.9v-3.8h-7.5v-1.1h9.5s4.4.4 4.4-7.5c0-7.1-3.9-6.9-3.9-6.9h-2.3v3.3s.1 3.9-3.8 3.9h-6.5s-3.7-.1-3.7 3.5v6.1s-.5 4.2 7.3 4.5zm4.1-2.2a1.1 1.1 0 1 1 0-2.2 1.1 1.1 0 0 1 0 2.2z"
      />
    </svg>
  );
}
