import { getApi } from "../hooks/usePanelApi";
import { readConfettiPalette, readCssVar } from "../theme/readCssVar";

type Particle = {
  x: number;
  y: number;
  vx: number;
  vy: number;
  w: number;
  h: number;
  color: string;
  rotation: number;
  rotationSpeed: number;
  life: number;
  maxLife: number;
};

let canvas: HTMLCanvasElement | null = null;
let ctx: CanvasRenderingContext2D | null = null;
let particles: Particle[] = [];
let raf = 0;

function ensureCanvas() {
  if (canvas) return;
  canvas = document.createElement("canvas");
  canvas.className = "confetti-burst-canvas";
  canvas.setAttribute("aria-hidden", "true");
  document.body.appendChild(canvas);
  ctx = canvas.getContext("2d");
  resizeCanvas();
  window.addEventListener("resize", resizeCanvas);
}

function resizeCanvas() {
  if (!canvas) return;
  const dpr = window.devicePixelRatio || 1;
  canvas.style.width = `${window.innerWidth}px`;
  canvas.style.height = `${window.innerHeight}px`;
  canvas.width = Math.floor(window.innerWidth * dpr);
  canvas.height = Math.floor(window.innerHeight * dpr);
  ctx?.setTransform(dpr, 0, 0, dpr, 0, 0);
}

function pickConfettiColor(): string {
  const palette = readConfettiPalette();
  if (palette.length === 0) return readCssVar("fg");
  return palette[Math.floor(Math.random() * palette.length)]!;
}

export function burstConfetti(x: number, y: number, count = 90) {
  ensureCanvas();
  for (let i = 0; i < count; i++) {
    const angle = Math.random() * Math.PI * 2;
    const speed = 5 + Math.random() * 12;
    particles.push({
      x,
      y,
      vx: Math.cos(angle) * speed,
      vy: Math.sin(angle) * speed - 5,
      w: 4 + Math.random() * 7,
      h: 5 + Math.random() * 9,
      color: pickConfettiColor(),
      rotation: Math.random() * Math.PI * 2,
      rotationSpeed: (Math.random() - 0.5) * 0.35,
      life: 0,
      maxLife: 55 + Math.random() * 45,
    });
  }
  if (!raf) {
    raf = requestAnimationFrame(tick);
  }
}

export function burstConfettiFromElement(el: HTMLElement | null) {
  if (!el) return;
  const rect = el.getBoundingClientRect();
  const x = rect.left + rect.width / 2;
  const y = rect.top + rect.height / 2;
  burstConfetti(x, y);
  const api = getApi();
  if (api && typeof api.burst_desktop_confetti === "function") {
    void api.burst_desktop_confetti(x, y);
  }
}

function tick() {
  if (!canvas || !ctx) return;
  ctx.clearRect(0, 0, window.innerWidth, window.innerHeight);

  particles = particles.filter((p) => {
    p.life += 1;
    p.vy += 0.38;
    p.vx *= 0.985;
    p.x += p.vx;
    p.y += p.vy;
    p.rotation += p.rotationSpeed;

    const alpha = 1 - p.life / p.maxLife;
    if (alpha <= 0) return false;

    ctx!.save();
    ctx!.translate(p.x, p.y);
    ctx!.rotate(p.rotation);
    ctx!.globalAlpha = alpha;
    ctx!.fillStyle = p.color;
    ctx!.fillRect(-p.w / 2, -p.h / 2, p.w, p.h);
    ctx!.restore();
    return true;
  });

  if (particles.length > 0) {
    raf = requestAnimationFrame(tick);
  } else {
    raf = 0;
    ctx.clearRect(0, 0, window.innerWidth, window.innerHeight);
  }
}
