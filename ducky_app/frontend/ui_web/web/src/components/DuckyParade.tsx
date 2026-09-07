import { BUNDLED_DUCKIES } from "../generated/bundledDuckies";

export type DuckyParadeSize = "md" | "sm" | "xs";

const FAMILY = ["leader", "mom", "boy", "girl"] as const;

interface DuckyParadeProps {
  size?: DuckyParadeSize;
  label?: string;
  className?: string;
}

/** Marching bundled duckies — the shared loading indicator (DuckyOS parade, our PNGs). */
export function DuckyParade({ size = "md", label, className }: DuckyParadeProps) {
  const ducks = BUNDLED_DUCKIES.slice(0, FAMILY.length);
  return (
    <div
      className={`ducky-parade ducky-parade--${size}${className ? ` ${className}` : ""}`}
      data-ducky-parade
      role="status"
      aria-label={label || "Loading"}
    >
      <span className="ducky-parade__track">
        {ducks.map((duck, i) => (
          <img
            key={duck.id}
            className={`ducky-parade__duck ducky-parade__duck--${FAMILY[i]}`}
            src={duck.url}
            alt=""
            draggable={false}
          />
        ))}
      </span>
      {label ? <span className="ducky-parade__label">{label}</span> : null}
    </div>
  );
}

interface DuckyParadeOverlayProps {
  size?: DuckyParadeSize;
  label?: string;
  cover?: boolean;
}

/** Pane-sized loader. `cover` pins over an already-positioned parent. */
export function DuckyParadeOverlay({ size = "md", label = "Loading", cover = false }: DuckyParadeOverlayProps) {
  return (
    <div className={`ducky-parade-overlay${cover ? " ducky-parade-overlay--cover" : ""}`}>
      <DuckyParade size={size} label={label} />
    </div>
  );
}
