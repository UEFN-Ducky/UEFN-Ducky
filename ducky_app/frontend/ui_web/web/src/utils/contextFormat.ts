export function fmtTokens(n: number): string {
  return Math.max(0, Math.round(n)).toLocaleString("en-US");
}

export function fmtCompactTokens(n: number): string {
  const v = Math.max(0, n);
  if (v >= 1_000_000) return `${(v / 1_000_000).toFixed(1).replace(/\.0$/, "")}M`;
  if (v >= 10_000) return `${(v / 1_000).toFixed(1).replace(/\.0$/, "")}K`;
  if (v >= 1_000) return `${(v / 1_000).toFixed(1)}K`;
  return fmtTokens(v);
}

export function fmtPercent(used: number, limit: number): number {
  if (limit <= 0) return 0;
  return Math.min(100, Math.round((used / limit) * 100));
}

export function fmtCostUsd(cost: number): string {
  if (cost >= 1) return `$${cost.toFixed(2)}`;
  if (cost >= 0.01) return `$${cost.toFixed(3)}`;
  if (cost > 0) return `$${cost.toFixed(4)}`;
  return "$0.00";
}
