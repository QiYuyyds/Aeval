"use client";

import { RunStatusBadge } from "@/components/run-status-badge";
import { Table, Td, Th } from "@/components/ui/primitives";
import { fmtDuration, fmtScore, fmtTime } from "@/lib/format";
import type { RunListItem } from "@/lib/types";
import Link from "next/link";

export function RunsTable({ runs, max = 20 }: { runs: RunListItem[]; max?: number }) {
  const rows = runs.slice(0, max);
  if (rows.length === 0) {
    return <div className="text-sm text-muted-foreground">还没有运行记录</div>;
  }
  return (
    <Table>
      <thead>
        <tr>
          <Th>Run</Th>
          <Th>Suite</Th>
          <Th>状态</Th>
          <Th>pass@1 (仅 valid)</Th>
          <Th>平均分</Th>
          <Th>任务数</Th>
          <Th>开始时间</Th>
          <Th>耗时</Th>
        </tr>
      </thead>
      <tbody>
        {rows.map((r) => (
          <tr key={r.run_id} className="hover:bg-muted/40">
            <Td className="mono">
              <Link className="text-primary hover:underline" href={`/runs/${r.run_id}`}>
                {r.run_id}
              </Link>
            </Td>
            <Td>{r.suite_name}</Td>
            <Td>
              <RunStatusBadge status={r.status} />
            </Td>
            <Td>
              {r.summary && r.summary.valid_trials === 0 ? (
                "证据不足"
              ) : (
                fmtScore(r.summary?.pass_at_k?.["1"])
              )}
              {(r.summary?.invalid_trials ?? 0) > 0 ? (
                <span
                  className="ml-1 text-xs text-warning"
                  title="评测侧故障导致的无效 trial, 已从分母排除"
                >
                  ⚠{r.summary?.invalid_trials}
                </span>
              ) : null}
            </Td>
            <Td>{fmtScore(r.summary?.avg_score)}</Td>
            <Td>{r.task_count}</Td>
            <Td className="text-muted-foreground">{fmtTime(r.started_at)}</Td>
            <Td>{fmtDuration(r.duration_ms)}</Td>
          </tr>
        ))}
      </tbody>
    </Table>
  );
}
