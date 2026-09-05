"use client";

import {
  Badge,
  Button,
  Card,
  CardContent,
  CardHeader,
  CardTitle,
  Label,
  Table,
  Td,
  Th,
} from "@/components/ui/primitives";
import { fmtCi, fmtDelta, fmtRate, fmtScore } from "@/lib/format";
import { useCompare, useRuns } from "@/lib/queries";
import type { ComparisonResponse, ComparisonRow, TaskDelta } from "@/lib/types";
import { useState } from "react";

export default function ComparePage() {
  const { data: runsData } = useRuns(100);
  const runs = (runsData?.runs ?? []).filter((r) => r.summary != null);
  const [runIdA, setRunIdA] = useState("");
  const [runIdB, setRunIdB] = useState("");
  const compare = useCompare();

  const comparison = compare.data?.comparison ?? null;

  return (
    <div className="flex flex-col gap-6">
      <div>
        <h1 className="text-xl font-semibold">A/B 对比</h1>
        <p className="text-sm text-muted-foreground">
          选择两个已完成的 run,对比全局指标与逐任务 delta
        </p>
      </div>

      <Card>
        <CardHeader>
          <CardTitle>选择 Runs</CardTitle>
        </CardHeader>
        <CardContent className="flex flex-col gap-3">
          <div className="grid gap-3 md:grid-cols-2">
            <div>
              <Label>Run A (基准)</Label>
              <RunSelect value={runIdA} onChange={setRunIdA} runs={runs} />
            </div>
            <div>
              <Label>Run B (对比)</Label>
              <RunSelect value={runIdB} onChange={setRunIdB} runs={runs} />
            </div>
          </div>
          <div>
            <Button
              disabled={!runIdA || !runIdB || runIdA === runIdB || compare.isPending}
              onClick={() => compare.mutate({ runIdA, runIdB })}
            >
              {compare.isPending ? "对比中…" : "对比"}
            </Button>
            {runIdA && runIdA === runIdB ? (
              <span className="ml-3 text-xs text-warning">两个 run 不能相同</span>
            ) : null}
          </div>
          {compare.isError ? (
            <div className="text-sm text-danger">
              对比失败: {String(compare.error)}
            </div>
          ) : null}
        </CardContent>
      </Card>

      {comparison ? <ComparisonResult data={compare.data!} /> : null}
    </div>
  );
}

function RunSelect({
  value,
  onChange,
  runs,
}: {
  value: string;
  onChange: (v: string) => void;
  runs: Array<{ run_id: string; suite_name: string; status: string }>;
}) {
  return (
    <select
      className="mono h-9 w-full rounded-lg border border-border bg-background px-3 text-sm outline-none focus:border-primary"
      value={value}
      onChange={(e) => onChange(e.target.value)}
    >
      <option value="">— 选择 run —</option>
      {runs.map((r) => (
        <option key={r.run_id} value={r.run_id}>
          {r.run_id} ({r.suite_name})
        </option>
      ))}
    </select>
  );
}

function ComparisonResult({ data }: { data: ComparisonResponse }) {
  const { comparison } = data;
  const kLabel = (key: string, prefix: string, at: boolean) =>
    `${at ? "pass@" : "pass^"}${key.startsWith(prefix) ? key.slice(prefix.length) : key}`;

  const globalRows: Array<{ label: string; row: ComparisonRow }> = [
    { label: "平均分", row: comparison.avg_score },
    ...Object.entries(comparison.pass_at_k).map(([key, row]) => ({
      label: kLabel(key, "pass_at_", true),
      row,
    })),
    ...Object.entries(comparison.pass_power_k).map(([key, row]) => ({
      label: kLabel(key, "pass_power_", false),
      row,
    })),
  ];

  return (
    <div className="flex flex-col gap-4">
      {!comparison.comparable ? (
        <div className="rounded-lg border border-warning/40 bg-warning/10 p-3 text-sm text-warning">
          不可比: {comparison.not_comparable_reason ?? "统计口径版本不同"} (A=v
          {comparison.statistics_version.a ?? "未知"} / B=v
          {comparison.statistics_version.b ?? "未知"})。下方差值仅供参考，不构成退化或提升结论。
        </div>
      ) : null}

      <Card>
        <CardHeader>
          <CardTitle>
            全局指标{" "}
            <span className="mono text-xs text-muted-foreground">
              A → B (delta · 仅区间不重叠才判方向)
            </span>
          </CardTitle>
        </CardHeader>
        <CardContent>
          <Table>
            <thead>
              <tr>
                <Th>指标</Th>
                <Th>Run A</Th>
                <Th>Run B</Th>
                <Th>Delta</Th>
                <Th>显著性</Th>
              </tr>
            </thead>
            <tbody>
              {globalRows.map((r) => (
                <DeltaRow key={r.label} label={r.label} row={r.row} />
              ))}
            </tbody>
          </Table>
        </CardContent>
      </Card>

      <div className="grid gap-4 md:grid-cols-2">
        <DeltaListCard
          title={`退化任务 (${comparison.regressions.length})`}
          items={comparison.regressions}
          tone="danger"
        />
        <DeltaListCard
          title={`提升任务 (${comparison.improvements.length})`}
          items={comparison.improvements}
          tone="success"
        />
      </div>

      <Card>
        <CardHeader>
          <CardTitle>逐任务分数</CardTitle>
        </CardHeader>
        <CardContent>
          <Table>
            <thead>
              <tr>
                <Th>Task</Th>
                <Th>A</Th>
                <Th>B</Th>
                <Th>Delta</Th>
                <Th>显著性</Th>
              </tr>
            </thead>
            <tbody>
              {Object.entries(comparison.tasks).map(([taskId, row]) => (
                <DeltaRow key={taskId} label={taskId} mono row={row} />
              ))}
            </tbody>
          </Table>
        </CardContent>
      </Card>
    </div>
  );
}

/** 单行对比: 方向性配色只在 significant 时出现 (D4) */
function DeltaRow({
  label,
  row,
  mono,
}: {
  label: string;
  row: ComparisonRow;
  mono?: boolean;
}) {
  const directional = row.significant === true && row.delta != null;
  const tone = directional
    ? row.delta! > 0
      ? "text-success"
      : "text-danger"
    : "text-muted-foreground";
  const ciA = fmtCi(row.a_ci);
  const ciB = fmtCi(row.b_ci);
  return (
    <tr>
      <Td className={mono ? "mono" : undefined}>{label}</Td>
      <Td>
        {fmtRate(row.a, row.extrapolated)}
        {ciA ? <span className="ml-1 text-xs text-muted-foreground">{ciA}</span> : null}
      </Td>
      <Td>
        {fmtRate(row.b, row.extrapolated)}
        {ciB ? <span className="ml-1 text-xs text-muted-foreground">{ciB}</span> : null}
      </Td>
      <Td>
        <span className={tone}>{fmtDelta(row.delta)}</span>
      </Td>
      <Td className="text-xs text-muted-foreground">
        {!row.comparable
          ? "口径不同, 不可比"
          : row.significant === null
            ? "区间缺失, 无法判定"
            : row.significant
              ? "显著 (区间不重叠)"
              : "不显著 (95% 区间重叠)"}
        {row.extrapolated ? " · 含外推值" : ""}
      </Td>
    </tr>
  );
}

function DeltaListCard({
  title,
  items,
  tone,
}: {
  title: string;
  items: TaskDelta[];
  tone: "danger" | "success";
}) {
  return (
    <Card>
      <CardHeader>
        <CardTitle>{title}</CardTitle>
      </CardHeader>
      <CardContent>
        {items.length === 0 ? (
          <div className="text-sm text-muted-foreground">无</div>
        ) : (
          <div className="flex flex-col gap-2">
            {items.map((d) => (
              <div
                key={d.task_id}
                className={`flex items-center justify-between rounded-lg border p-2 ${
                  tone === "danger" ? "border-danger/40 bg-danger/10" : "border-success/40 bg-success/10"
                }`}
              >
                <span className="mono text-sm">{d.task_id}</span>
                <span className="flex items-center gap-2 text-xs text-muted-foreground">
                  {fmtScore(d.a)} → {fmtScore(d.b)}
                  <Badge tone={tone}>{fmtDelta(d.delta)}</Badge>
                </span>
              </div>
            ))}
          </div>
        )}
      </CardContent>
    </Card>
  );
}
