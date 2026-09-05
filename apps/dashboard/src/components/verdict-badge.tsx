"use client";

import { Badge } from "@/components/ui/primitives";
import type { TrialVerdict } from "@/lib/types";

const TONES: Record<TrialVerdict, "success" | "warning" | "primary"> = {
  valid: "success",
  invalid: "warning",
  pending: "primary",
};

const LABELS: Record<TrialVerdict, string> = {
  valid: "有效",
  invalid: "评测无效",
  pending: "待人工评分",
};

/**
 * trial / grader 结论徽标。invalid 与 pending 不是 agent 表现结论,
 * 必须与「失败」在视觉上分开, 否则判分器故障会被读成能力退化。
 */
export function VerdictBadge({
  verdict,
  invalidReason,
}: {
  verdict: TrialVerdict;
  invalidReason?: string | null;
}) {
  return (
    <Badge tone={TONES[verdict]} title={invalidReason ?? undefined}>
      {LABELS[verdict]}
      {invalidReason ? ` · ${invalidReason}` : ""}
    </Badge>
  );
}
