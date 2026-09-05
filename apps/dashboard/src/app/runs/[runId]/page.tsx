"use client";

import { RunStatusBadge } from "@/components/run-status-badge";
import { StatCard } from "@/components/stat-card";
import { VerdictBadge } from "@/components/verdict-badge";
import {
  Badge,
  Card,
  CardContent,
  CardHeader,
  CardTitle,
  Table,
  Td,
  Th,
} from "@/components/ui/primitives";
import { fmtCi, fmtDuration, fmtPct, fmtRate, fmtScore, fmtTime } from "@/lib/format";
import { useRun, useRunTrials } from "@/lib/queries";
import { subscribeRunEvents } from "@/lib/sse";
import type {
  PassKEstimate,
  RunDetail,
  RunEvent,
  RunSummaryData,
  TaskSummary,
  TrialFull,
  TrialLite,
} from "@/lib/types";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useEffect, useRef, useState } from "react";

export default function RunReportPage() {
  const params = useParams<{ runId: string }>();
  const runId = params.runId;
  const { data: run, isLoading, error, refetch } = useRun(runId);
  const { data: trialsData } = useRunTrials(runId);
  const [events, setEvents] = useState<RunEvent[]>([]);
  const [connected, setConnected] = useState(false);
  // 快照+增量协议: 挂载即拉全量快照 (useQuery),running 时订阅 SSE 增量;
  // 断线 onError → 失效快照查询强制重拉,再由 EventSource 自动重连
  const qcInvalidation = useRef<(() => void) | null>(null);

  const running = run?.status === "running" || run?.status === "pending";

  useEffect(() => {
    qcInvalidation.current = () => {
      void refetch();
    };
  }, [refetch]);

  useEffect(() => {
    if (!running) return;
    setEvents([]);
    const unsubscribe = subscribeRunEvents(runId, {
      onEvent: (event) => {
        setEvents((prev) => [...prev.slice(-99), event]);
        if (
          event.type === "task_complete" ||
          event.type === "run_complete" ||
          event.type === "error"
        ) {
          qcInvalidation.current?.();
        }
      },
      onError: () => {
        setConnected(false);
        qcInvalidation.current?.(); // 快照自愈
      },
    });
    setConnected(true);
    return () => {
      unsubscribe();
      setConnected(false);
    };
  }, [runId, running]);

  if (isLoading) return <div className="text-sm text-muted-foreground">加载中…</div>;
  if (error || !run) {
    return <div className="text-sm text-danger">Run 不存在或后端未连接 ({String(error)})</div>;
  }

  const summary = run.summary;
  const taskIds = Object.keys(run.trials);

  return (
    <div className="flex flex-col gap-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="mono text-xl font-semibold">{run.run_id}</h1>
          <p className="flex items-center gap-2 text-sm text-muted-foreground">
            suite <Link className="text-primary hover:underline" href={`/suites/${encodeURIComponent(run.suite_name)}`}>{run.suite_name}</Link>
            · <RunStatusBadge status={run.status} />
            · {fmtTime(run.started_at)} · 耗时 {fmtDuration(run.duration_ms)}
          </p>
        </div>
        {running ? (
          <div className="flex items-center gap-2 text-xs text-muted-foreground">
            <span className={connected ? "text-success" : "text-warning"}>
              {connected ? "● 实时更新已连接" : "○ 重连中 (快照恢复)"}
            </span>
          </div>
        ) : null}
      </div>

      {run.error ? (
        <div className="rounded-lg border border-danger/40 bg-danger/10 p-3 text-sm text-danger">
          Run error: {run.error}
        </div>
      ) : null}

      <CaliberBanner run={run} />

      <div className="grid grid-cols-2 gap-4 lg:grid-cols-5">
        <StatCard
          label="pass@1"
          value={fmtRate(summary?.pass_at_k?.["1"], summary?.estimates?.["1"]?.extrapolated)}
          hint={rateHint(summary?.estimates?.["1"], summary?.valid_trials)}
        />
        <StatCard
          label={`pass^${maxK(summary)}`}
          value={fmtRate(
            summary?.pass_power_k?.[String(maxK(summary))],
            summary?.power_estimates?.[String(maxK(summary))]?.extrapolated,
          )}
          hint={rateHint(
            summary?.power_estimates?.[String(maxK(summary))],
            summary?.valid_trials,
          )}
        />
        <StatCard
          label="平均分"
          value={fmtScore(summary?.avg_score)}
          hint={
            summary?.score_distribution
              ? `worst_of_n ${fmtScore(summary.score_distribution.worst_of_n)} · ` +
                `95% ${fmtCi([summary.score_distribution.ci_low, summary.score_distribution.ci_high]) ?? "—"}`
              : "有效 trial 的加权分均值"
          }
        />
        <StatCard
          label="分母 (valid/invalid/pending)"
          value={denominatorText(summary)}
          hint="通过率与平均分只在 valid 上计算; * = 外推值"
        />
        <StatCard
          label="进度"
          value={running ? `${taskProgressPct(events, run.trials)}%` : `${taskIds.length}/${summary?.total_tasks ?? taskIds.length}`}
          hint={running ? "实时 (SSE)" : "已完成"}
        />
      </div>

      {running ? <LiveEventFeed events={events} /> : null}

      <Card>
        <CardHeader>
          <CardTitle>任务结果</CardTitle>
        </CardHeader>
        <CardContent>
          <TaskResultsTable
            runId={run.run_id}
            taskIds={taskIds}
            trials={run.trials}
            summaries={summary?.task_summaries}
            running={running}
          />
        </CardContent>
      </Card>

      {!running ? <FailedTrialsCard runId={run.run_id} trialsData={trialsData?.trials} /> : null}
    </div>
  );
}

function maxK(summary: RunSummaryData | null | undefined): number {
  const keys = Object.keys(summary?.pass_at_k ?? {}).map(Number).filter((n) => !Number.isNaN(n));
  return keys.length ? Math.max(...keys) : 1;
}

function rateHint(
  est: PassKEstimate | undefined,
  validTrials: number | null | undefined,
): string {
  if (!est) return `分母: ${validTrials ?? "—"} 个 valid trial`;
  const ci = fmtCi([est.p_lower_bound, est.p_upper_bound]);
  const method =
    est.method === "extrapolated"
      ? "外推 (k 超出实测样本)"
      : est.method === "insufficient_data"
        ? "证据不足"
        : "实测";
  return `${method} n=${est.n} · ${est.successes} 成功${ci ? ` · 95% ${ci}` : ""}`;
}

function denominatorText(summary: RunSummaryData | null | undefined): string {
  if (!summary) return "—";
  return `${summary.valid_trials ?? "—"} / ${summary.invalid_trials ?? "—"} / ${summary.pending_trials ?? "—"}`;
}

/** 口径声明: 历史 run 不回算, 因此版本可能为 null, 必须显式说明其含义。 */
function CaliberBanner({ run }: { run: RunDetail }) {
  const summary = run.summary;
  const total = summary?.total_trials ?? 0;
  const invalid = summary?.invalid_trials ?? 0;
  const mostlyInvalid = total > 0 && invalid / total > 0.5;
  return (
    <div
      className={`rounded-lg border p-3 text-xs ${
        mostlyInvalid ? "border-warning/40 bg-warning/10 text-warning" : "border-border text-muted-foreground"
      }`}
    >
      <span className="mono">统计口径 v{run.statistics_version ?? "未知 (历史 run, 不回算)"}</span>
      {" · pass@k 为有限样本无偏估计, 分母仅含 valid trial"}
      {mostlyInvalid ? (
        <div className="mt-1 font-medium">
          本次 {invalid}/{total} 个 trial 判为评测无效 —— 通过率下降反映的是评测故障,
          不是 agent 退化；请先检查 grader/judge 配置。
        </div>
      ) : null}
    </div>
  );
}

function taskProgressPct(events: RunEvent[], trials: Record<string, TrialLite[]>): number {
  // 运行中: 已收到 task_complete 的 task 数 / 已知 task 数 (从 trial_start 推断总数)
  const started = new Set<string>();
  const completed = new Set<string>();
  for (const e of events) {
    if (e.type === "task_start" && e.task_id) started.add(e.task_id);
    if (e.type === "task_complete" && e.task_id) completed.add(e.task_id);
  }
  const known = new Set([...Object.keys(trials), ...started]);
  if (known.size === 0) return 0;
  return Math.round((completed.size / known.size) * 100);
}

function LiveEventFeed({ events }: { events: RunEvent[] }) {
  if (events.length === 0) {
    return <div className="text-xs text-muted-foreground">等待事件…</div>;
  }
  const LABEL: Record<string, string> = {
    task_start: "任务开始",
    trial_start: "trial 开始",
    trial_complete: "trial 完成",
    task_complete: "任务完成",
    run_complete: "运行完成",
    error: "错误",
  };
  return (
    <Card>
      <CardHeader>
        <CardTitle>实时事件</CardTitle>
      </CardHeader>
      <CardContent className="mono max-h-40 overflow-y-auto text-xs">
        {events.slice(-12).map((e, i) => (
          <div key={i} className="py-0.5 text-muted-foreground">
            <span className="text-primary">{LABEL[e.type] ?? e.type}</span>
            {e.task_id ? ` ${e.task_id}` : ""}
            {e.trial_index != null ? ` #${e.trial_index}` : ""}
            {e.type === "trial_complete" ? (e.success ? " ✓" : " ✗") : ""}
            {e.type === "error" && e.error ? ` — ${String(e.error)}` : ""}
          </div>
        ))}
      </CardContent>
    </Card>
  );
}

function TaskResultsTable({
  runId,
  taskIds,
  trials,
  summaries,
  running,
}: {
  runId: string;
  taskIds: string[];
  trials: Record<string, TrialLite[]>;
  summaries?: TaskSummary[];
  running: boolean;
}) {
  if (taskIds.length === 0) {
    return <div className="text-sm text-muted-foreground">尚无任务数据</div>;
  }
  const byTask = new Map((summaries ?? []).map((ts) => [ts.task_id, ts]));
  return (
    <Table>
      <thead>
        <tr>
          <Th>Task</Th>
          <Th>pass@1 (实测)</Th>
          <Th>平均分</Th>
          <Th>分母 valid/invalid/待评</Th>
          <Th>状态</Th>
          <Th>Trials</Th>
        </tr>
      </thead>
      <tbody>
        {taskIds.map((taskId) => {
          const taskTrials = trials[taskId] ?? [];
          const ts = byTask.get(taskId);
          const est = ts?.estimates?.["1"];
          const invalidReasons = Object.values(ts?.invalid_reasons ?? {});
          return (
            <tr key={taskId}>
              <Td className="mono">{taskId}</Td>
              <Td
                title={
                  est
                    ? `n=${est.n} · ${est.successes} 成功 · 95% ${
                        fmtCi([est.p_lower_bound, est.p_upper_bound]) ?? "—"
                      }`
                    : undefined
                }
              >
                {ts ? (
                  fmtRate(ts.pass_at_k?.["1"], est?.extrapolated)
                ) : (
                  <span className="text-muted-foreground">
                    {liveRate(taskTrials, running)}
                  </span>
                )}
              </Td>
              <Td>{fmtScore(ts?.avg_score)}</Td>
              <Td className="mono text-xs">
                {ts
                  ? `${ts.valid_trials ?? "—"} / ${ts.invalid_trials ?? "—"} / ${ts.pending_trials.length}`
                  : "—"}
                {invalidReasons.length > 0 ? (
                  <div className="text-warning" title={invalidReasons.join(", ")}>
                    invalid: {[...new Set(invalidReasons)].join(", ")}
                  </div>
                ) : null}
              </Td>
              <Td>
                <TaskVerdictLabel ts={ts} taskTrials={taskTrials} running={running} />
              </Td>
              <Td>
                <div className="flex flex-wrap gap-1">
                  {taskTrials.map((t) => (
                    <Link
                      key={t.trial_index}
                      href={`/runs/${runId}/trials/${taskId}/${t.trial_index}`}
                      className="inline-flex"
                      title={t.invalid_reason ?? undefined}
                    >
                      <Badge
                        tone={
                          t.verdict === "invalid"
                            ? "warning"
                            : t.verdict === "pending"
                              ? "primary"
                              : t.success
                                ? "success"
                                : "danger"
                        }
                      >
                        #{t.trial_index}{" "}
                        {t.verdict === "invalid"
                          ? "⚠"
                          : t.verdict === "pending"
                            ? "…"
                            : t.success
                              ? "✓"
                              : "✗"}
                      </Badge>
                    </Link>
                  ))}
                  {taskTrials.length === 0 ? (
                    <span className="text-xs text-muted-foreground">等待中…</span>
                  ) : null}
                </div>
              </Td>
            </tr>
          );
        })}
      </tbody>
    </Table>
  );
}

/** 运行中尚无汇总时的实时比例 (明确标注非最终口径) */
function liveRate(taskTrials: TrialLite[], running: boolean): string {
  const judged = taskTrials.filter((t) => t.verdict === "valid");
  if (judged.length === 0) return "证据不足";
  const rate = fmtPct(judged.filter((t) => t.success).length / judged.length);
  return running ? `${rate} (实时)` : rate;
}

function TaskVerdictLabel({
  ts,
  taskTrials,
  running,
}: {
  ts: TaskSummary | undefined;
  taskTrials: TrialLite[];
  running: boolean;
}) {
  if (!ts) {
    return taskTrials.length === 0 || running ? (
      <Badge tone="primary">运行中</Badge>
    ) : (
      <Badge tone="muted">无汇总</Badge>
    );
  }
  const pending = ts.pending_trials.length;
  const invalid = ts.invalid_trials ?? 0;
  if (!ts.valid_trials) {
    return (
      <Badge tone={pending > 0 ? "primary" : "warning"} title="通过率分母为空, 不构成 agent 表现结论">
        {pending > 0 ? `待人工评分 (${pending})` : "证据不足"}
      </Badge>
    );
  }
  if (invalid > 0) {
    return (
      <Badge tone="warning" title="部分 trial 为评测侧故障, 已从分母排除">
        含 {invalid} 个无效 trial
      </Badge>
    );
  }
  return ts.failures.length === 0 ? (
    <Badge tone="success">通过</Badge>
  ) : (
    <Badge tone="danger">
      失败 {ts.failures.length}/{ts.valid_trials}
    </Badge>
  );
}

function FailedTrialsCard({
  runId,
  trialsData,
}: {
  runId: string;
  trialsData?: Record<string, TrialFull[]>;
}) {
  if (!trialsData) return null;
  const failed: Array<{ taskId: string; trial: TrialFull }> = [];
  const unevaluated: Array<{ taskId: string; trial: TrialFull }> = [];
  for (const [taskId, trials] of Object.entries(trialsData)) {
    for (const t of trials) {
      // invalid / pending 是评测侧状态, 与 agent 失败分列, 不混进同一张清单
      if (t.verdict !== "valid") unevaluated.push({ taskId, trial: t });
      else if (!t.success) failed.push({ taskId, trial: t });
    }
  }
  if (failed.length === 0 && unevaluated.length === 0) return null;

  return (
    <Card>
      <CardHeader>
        <CardTitle>
          失败 Trial ({failed.length})
          {unevaluated.length > 0 ? (
            <span className="ml-2 text-xs text-warning">
              另有 {unevaluated.length} 个 trial 未构成 agent 结论
            </span>
          ) : null}
        </CardTitle>
      </CardHeader>
      <CardContent className="flex flex-col gap-3">
        {[...failed, ...unevaluated].map(({ taskId, trial }) => {
          const shownGraders =
            trial.verdict === "valid"
              ? trial.grader_results.filter((g) => !g.passed)
              : trial.grader_results;
          return (
            <div key={`${taskId}-${trial.trial_index}`} className="rounded-lg border border-border p-3">
              <div className="flex items-center justify-between">
                <Link
                  className="mono text-sm text-primary hover:underline"
                  href={`/runs/${runId}/trials/${taskId}/${trial.trial_index}`}
                >
                  {taskId} #{trial.trial_index}
                </Link>
                <span className="flex items-center gap-2">
                  <VerdictBadge verdict={trial.verdict} invalidReason={trial.invalid_reason} />
                  <span className="text-xs text-muted-foreground">
                    {fmtDuration(trial.duration_ms)}
                  </span>
                </span>
              </div>
              {trial.error ? (
                <div className="mt-1 text-xs text-danger">error: {trial.error}</div>
              ) : null}
              {shownGraders.length > 0 ? (
                <ul className="mt-1 flex flex-col gap-1">
                  {shownGraders.map((g) => (
                    <li key={g.grader_name} className="text-xs text-muted-foreground">
                      <span
                        className={
                          g.verdict === "valid" && !g.passed ? "text-danger" : "text-warning"
                        }
                      >
                        {g.grader_name}
                      </span>{" "}
                      ({fmtScore(g.score)}): {g.explanation}
                    </li>
                  ))}
                </ul>
              ) : null}
            </div>
          );
        })}
      </CardContent>
    </Card>
  );
}
