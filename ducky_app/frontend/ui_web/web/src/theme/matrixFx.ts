/** Built-in Matrix rain canvas effect for `#ducky-fx-root`. */

const GLYPHS =
  "ｱｲｳｴｵｶｷｸｹｺｻｼｽｾｿﾀﾁﾂﾃﾄﾅﾆﾇﾈﾉﾊﾋﾌﾍﾎﾏﾐﾑﾒﾓﾔﾕﾖﾗﾘﾙﾚﾛﾜﾝ0123456789ABCDEF<>{}[]|/\\";

export const MATRIX_EFFECT_ID = "matrix";

export function mountMatrixFx(root: HTMLElement): () => void {
  const reduced =
    typeof window.matchMedia === "function" &&
    window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  if (reduced) {
    root.replaceChildren();
    return () => {
      root.replaceChildren();
    };
  }

  const canvas = document.createElement("canvas");
  canvas.className = "ducky-fx-canvas ducky-fx-canvas--matrix";
  canvas.setAttribute("aria-hidden", "true");
  root.replaceChildren(canvas);

  const ctx = canvas.getContext("2d");
  if (!ctx) {
    return () => {
      root.replaceChildren();
    };
  }

  const fontSize = 14;
  let columns = 0;
  let drops: number[] = [];
  let raf = 0;
  let last = 0;

  const resize = () => {
    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    const w = window.innerWidth;
    const h = window.innerHeight;
    canvas.width = Math.max(1, Math.floor(w * dpr));
    canvas.height = Math.max(1, Math.floor(h * dpr));
    canvas.style.width = `${w}px`;
    canvas.style.height = `${h}px`;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    columns = Math.max(1, Math.floor(w / fontSize));
    drops = Array.from({ length: columns }, () => Math.random() * (h / fontSize));
  };

  const draw = (ts: number) => {
    raf = window.requestAnimationFrame(draw);
    if (ts - last < 33) return;
    last = ts;
    const w = window.innerWidth;
    const h = window.innerHeight;
    ctx.fillStyle = "rgba(0, 0, 0, 0.08)";
    ctx.fillRect(0, 0, w, h);
    ctx.font = `${fontSize}px ui-monospace, SFMono-Regular, Menlo, Consolas, monospace`;
    for (let i = 0; i < drops.length; i++) {
      const ch = GLYPHS[(Math.random() * GLYPHS.length) | 0] || "0";
      const x = i * fontSize;
      const y = drops[i]! * fontSize;
      ctx.fillStyle = i % 7 === 0 ? "rgba(180, 255, 200, 0.55)" : "rgba(0, 220, 70, 0.35)";
      ctx.fillText(ch, x, y);
      if (y > h && Math.random() > 0.975) drops[i] = 0;
      drops[i]! += 1;
    }
  };

  resize();
  ctx.fillStyle = "#000";
  ctx.fillRect(0, 0, window.innerWidth, window.innerHeight);
  raf = window.requestAnimationFrame(draw);
  window.addEventListener("resize", resize);

  return () => {
    window.cancelAnimationFrame(raf);
    window.removeEventListener("resize", resize);
    root.replaceChildren();
  };
}
