/** Aeval REST/SSE API 类型 — 与 agent_eval.api.routes (aeval/packages/agent-eval) 返回契约对齐。 */

export type RunStatus = "pending" | "running" | "completed" | "failed" | "cancelled";

/** trial / grader 结论分类 (与 core.types.TrialVerdict 同步) */
export type TrialVerdict = "valid" | "invalid" | "pending";

/** 一个 k 值的通过率估计及其口径元数据 (core.types.PassKEstimate) */
export interface PassKEstimate {
  k: number;
  n: number;
  successes: number;
  /** null = insufficient_data (分母为 0) */
  value: number | null;
  method: "measured" | "extrapolated" | "insufficient_data";
  extrapolated: boolean;
  p_point: number | null;
  p_lower_bound: number | null;
  p_upper_bound: number | null;
  ci_level: number;
}

/** 连续分数分布摘要 (core.types.ScoreDistribution) */
export interface ScoreDistribution {
  n: number;
  mean: number | null;
  std_dev: number | null;
  worst_of_n: number | null;
  ci_low: number | null;
  ci_high: number | null;
  ci_level: number;
  method: "bootstrap" | "insufficient_data";
}

export interface GraderResultLite {
  grader_name: string;
  score: number;
  passed: boolean;
  explanation: string;
  verdict: TrialVerdict;
  invalid_reason: string | null;
}

export interface TrialLite {
  trial_index: number;
  trace_id: string;
  success: boolean;
  score: number;
  duration_ms: number;
  error: string | null;
  verdict: TrialVerdict;
  invalid_reason: string | null;
  grader_results: GraderResultLite[];
}

export interface TaskSummary {
  task_id: string;
  task_description: string;
  total_trials: number;
  /** {k: rate}; null = insufficient_data (无有效 trial 进入分母) */
  pass_at_k: Record<string, number | null>;
  pass_power_k: Record<string, number | null>;
  estimates: Record<string, PassKEstimate>;
  power_estimates: Record<string, PassKEstimate>;
  /** null = 该 run 的统计口径未知 (历史行) */
  valid_trials: number | null;
  invalid_trials: number | null;
  avg_score: number | null;
  score_distribution: ScoreDistribution | null;
  avg_metrics: Record<string, number>;
  failures: number[];
  invalid_trial_indices: number[];
  /** trial 索引 (字符串) → 评测侧失败原因 */
  invalid_reasons: Record<string, string>;
  pending_trials: number[];
  consistent: boolean | null;
  score_std_dev: number | null;
  sample_sufficient: boolean | null;
}

export interface RunSummaryData {
  total_tasks: number;
  total_trials: number;
  pass_at_k: Record<string, number | null>;
  pass_power_k: Record<string, number | null>;
  estimates: Record<string, PassKEstimate>;
  power_estimates: Record<string, PassKEstimate>;
  valid_trials: number | null;
  invalid_trials: number | null;
  pending_trials: number | null;
  avg_score: number | null;
  score_distribution: ScoreDistribution | null;
  avg_metrics: Record<string, number>;
  task_summaries: TaskSummary[];
  failures: string[];
  saturation: Record<string, unknown>;
}

export interface RunListItem {
  run_id: string;
  suite_name: string;
  status: RunStatus;
  started_at: number;
  completed_at: number | null;
  duration_ms: number | null;
  task_count: number;
  statistics_version: string | null;
  summary: RunSummaryData | null;
}

export interface RunDetail {
  run_id: string;
  suite_name: string;
  status: RunStatus;
  started_at: number;
  completed_at: number | null;
  duration_ms: number | null;
  error: string | null;
  statistics_version: string | null;
  trials: Record<string, TrialLite[]>;
  summary: RunSummaryData | null;
}

export interface SuiteListItem {
  name: string;
  description: string;
  task_count: number;
  metadata: Record<string, unknown>;
}

export interface GraderConfig {
  type: string;
  name: string;
  weight: number;
  required: boolean;
  config: Record<string, unknown>;
}

export interface SuiteTask {
  id: string;
  description: string;
  prompt: string;
  graders: GraderConfig[];
  env: Record<string, unknown>;
  max_trials: number;
}

export interface SuiteDetail {
  name: string;
  description: string;
  version: string;
  tasks: SuiteTask[];
  metadata: Record<string, unknown>;
}

/** GET /runs/{id}/trials 的完整 TrialResult (含 transcript/outcome/metrics) */
export interface TrialFull {
  trial_index: number;
  trace_id: string;
  success: boolean;
  metrics: Record<string, number>;
  transcript: TranscriptEntry[];
  outcome: {
    conversation_id?: string;
    run_ids?: string[];
    files?: Record<string, string>;
    artifacts?: Array<Record<string, unknown>>;
    seed_files?: string[];
    trace_id_unavailable?: string;
  } & Record<string, unknown>;
  duration_ms: number;
  error: string | null;
  verdict: TrialVerdict;
  invalid_reason: string | null;
  grader_results: GraderResultFull[];
}

export interface GraderResultFull extends GraderResultLite {
  grader_type: string;
  details: Record<string, unknown>;
  confidence: number;
  uncertainty: number;
  sample_count: number;
  duration_ms: number;
}

export interface TranscriptEntry {
  id: string;
  role: string;
  agent_id: string | null;
  content: string;
  status: string;
  run_id: string | null;
  created_at: number;
  parts?: Array<Record<string, unknown>>;
}

/** SSE 事件载荷 (GET /runs/{id}/stream) */
export interface RunEvent {
  type:
    | "task_start"
    | "trial_start"
    | "trial_complete"
    | "task_complete"
    | "run_complete"
    | "error";
  run_id: string;
  timestamp: number;
  task_id?: string;
  trial_index?: number;
  status?: RunStatus;
  error?: string | null;
  summary?: RunSummaryData | null;
  [key: string]: unknown;
}

/** 单行对比: 既有 a/b/delta 名称与语义不变, 值可为 null (证据不足) */
export interface ComparisonRow {
  a: number | null;
  b: number | null;
  delta: number | null;
  a_ci: [number | null, number | null] | null;
  b_ci: [number | null, number | null] | null;
  /** null = 任一区间缺失, 无法判定重叠 */
  intervals_overlap: boolean | null;
  /** true 仅在两 run 口径一致且区间不重叠时成立 */
  significant: boolean | null;
  extrapolated: boolean;
  comparable: boolean;
}

export interface ComparisonResponse {
  run_a: { run_id: string; suite_name: string; started_at: number };
  run_b: { run_id: string; suite_name: string; started_at: number };
  comparison: {
    pass_at_k: Record<string, ComparisonRow>;
    pass_power_k: Record<string, ComparisonRow>;
    avg_score: ComparisonRow;
    regressions: TaskDelta[];
    improvements: TaskDelta[];
    tasks: Record<string, ComparisonRow>;
    statistics_version: { a: string | null; b: string | null };
    comparable: boolean;
    not_comparable_reason: string | null;
  };
}

export interface TaskDelta {
  task_id: string;
  a: number;
  b: number;
  delta: number;
}

// ── 数据集 (change ③ /api/eval/datasets) ─────────────────────────────────────

export interface DatasetSummary {
  id: string;
  name: string;
  description: string;
  version: string;
  tags: string[];
  capability_map: Record<string, number>;
  item_count: number;
  created_at: number;
  updated_at: number;
}

export interface DatasetItem {
  id: string;
  prompt: string;
  description: string;
  graders: GraderConfig[];
  env: Record<string, unknown>;
  metadata: Record<string, unknown>;
  source_type: string;
  source_ref: string;
  created_at: number;
}

export interface DatasetChangeLogEntry {
  version: string;
  change_type: string;
  note: string;
  at: number;
  item_count: number;
}

export interface DatasetDetail extends DatasetSummary {
  items: DatasetItem[];
  metadata: Record<string, unknown>;
  change_log: DatasetChangeLogEntry[];
}

export interface QualityIssue {
  code: string;
  severity: string;
  item_id: string;
  message: string;
}

export interface QualityReport {
  total_items: number;
  ok: boolean;
  error_count: number;
  warning_count: number;
  errors: QualityIssue[];
  warnings: QualityIssue[];
}

export interface CoverageReport {
  total_items: number;
  untagged_items: number;
  coverage: Record<string, number>;
  insufficient: Array<{ capability: string; item_count: number; coverage: number }>;
}

export interface SkippedEntry {
  trace_id?: string;
  reason?: string;
  index?: number;
  error?: string;
  [key: string]: unknown;
}

/** POST /datasets/{ref}/from-trace 响应 */
export interface FromTraceResponse {
  mining: {
    strategy: string;
    candidates: number;
    inspected: number;
    mined: number;
    skipped_count: number;
    skipped: SkippedEntry[];
    item_ids: string[];
  };
  merged: number;
  merged_skipped: SkippedEntry[];
  dataset_id: string;
  item_count: number;
}

/** POST /datasets/{ref}/from-llm 响应 */
export interface FromLLMResponse {
  generation: {
    scenario: string;
    requested: number;
    generated: number;
    invalid_count: number;
    invalid: SkippedEntry[];
    item_ids: string[];
  };
  dataset_id: string;
  item_count: number;
}

/** POST /datasets/{ref}/regression-extract 响应 */
export interface RegressionExtractResponse {
  extraction: { extracted: number; skipped: SkippedEntry[]; [key: string]: unknown };
  merge: { merged: number; merged_skipped: SkippedEntry[]; [key: string]: unknown };
  version: string;
  bumped: boolean;
  dataset_id: string;
  item_count: number;
}

// ── Task 库 (跨 suite) ───────────────────────────────────────────────────────

export interface TaskListItem {
  id: string;
  description: string;
  suite_name: string;
  max_trials: number;
  grader_count: number;
}

export interface TaskFullDef extends SuiteTask {
  score_strategy: string;
  score_threshold: number;
  tracked_metrics: string[];
}

export interface TaskDetailResponse {
  task: TaskFullDef;
  suite_name: string;
}

/** GET /tasks/{id}/history 单条 — 该 task 在一次 run 中的聚合结果 */
export interface TaskHistoryEntry {
  run_id: string;
  suite_name: string;
  started_at: number;
  trials_passed: number;
  trials_total: number;
  valid_trials: number;
  invalid_trials: number;
  pending_trials: number;
  /** null = 无有效 trial 进入分母 (insufficient_data) */
  avg_score: number | null;
  graders: Record<string, number>;
}

export interface TaskHistoryResponse {
  task_id: string;
  suite_name: string;
  history: TaskHistoryEntry[];
}
