/** cn / 格式化工具。 */

import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

export function fmtScore(v: number | null | undefined): string {
  if (v == null) return "—";
  return v.toFixed(3);
}

/** 通过率: null = 证据不足 (分母无 valid trial), 外推值以 * 标注不冒充实测 */
export function fmtRate(v: number | null | undefined, extrapolated = false): string {
  if (v == null) return "证据不足";
  return `${(v * 100).toFixed(1)}%${extrapolated ? "*" : ""}`;
}

/** 置信区间文本; 两端皆缺时返回 null (不猜测区间) */
export function fmtCi(
  ci: readonly (number | null)[] | null | undefined,
  digits = 3,
): string | null {
  if (!ci) return null;
  const [lo, hi] = ci;
  if (lo == null && hi == null) return null;
  const show = (v: number | null) => (v == null ? "—" : v.toFixed(digits));
  return `[${show(lo)}, ${show(hi)}]`;
}

export function fmtDelta(v: number | null | undefined): string {
  if (v == null) return "证据不足";
  const sign = v > 0 ? "+" : "";
  return `${sign}${v.toFixed(3)}`;
}

export function fmtDuration(ms: number | null | undefined): string {
  if (ms == null) return "—";
  if (ms < 1000) return `${Math.round(ms)}ms`;
  const s = ms / 1000;
  if (s < 60) return `${s.toFixed(1)}s`;
  const m = Math.floor(s / 60);
  return `${m}m ${Math.round(s - m * 60)}s`;
}

export function fmtTime(ts: number | null | undefined): string {
  if (!ts) return "—";
  return new Date(ts).toLocaleString();
}

export function fmtPct(v: number | null | undefined): string {
  if (v == null) return "—";
  return `${(v * 100).toFixed(0)}%`;
}
