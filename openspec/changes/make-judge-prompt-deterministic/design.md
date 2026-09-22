## Context

`graders/model_based.py` 的 `_build_prompt` 是框架内唯一把集合派生内容拼进判分输入的地方（`model_based.py:158` 的 `list(set(...))`）。变更④ 立约"采集与评分分离、可对同一批归档字节重评"，其成立前提是判分输入可复现；本变更补齐这个前提。动机与影响面见 `proposal.md`，行为要求见 `specs/graders/spec.md`。

约束（决定方案形状，不是复述提案）：

- 判分输入一旦变化，`verdict_drift` 读到的翻转必须能归因到**唯一**原因，否则④ 的审计工具本身被污染。因此版本提升与修复必须同批落地，且不能夹带其它判分行为改动。
- `statistics_version` 管的是分母口径（pass@1 语义、invalid 归属），本变更不动分母，因此**不得**顺带提升它。
- 0.3.0 未发布到 PyPI，历史 run 只存在于本地库，无跨版本兼容负担。

## Goals / Non-Goals

**Goals:**

- 判分输入成为（归档证据, rubric, dimensions, 判分配置）的纯函数。
- 用一条测试把"集合序泄进判分输入"这一**类**缺陷钉住，使未来新增判分路径自动受检。
- 把审计中发现的隐性不变量显式化（见 D4），只加测试、不改行为。

**Non-Goals:**

- 不做判分器呈现不变性测量（bias probe）。那是后继变更，本变更只负责让它的前提成立。
- 不改 judge 提示词的**内容**：措辞、锚定模板预填值、系统提示词、无截断策略，一律保持原样。它们各自可能是问题，但都不是"可复现性"问题，混进来会让 verdict 翻转无法归因。
- 不引入全局 `PYTHONHASHSEED` 约束，不要求宿主以固定 hash 种子运行。
- 不处理呈现层（CLI/看板列表）的次序抖动 —— 它不影响判定。

## Decisions

### D1 修在构造点，不修在运行时环境

`list(set(x))` → `sorted(set(x))`。

*备选*：要求评测进程以 `PYTHONHASHSEED=0` 启动。否决 —— 那把可复现性变成部署条件而非代码性质，宿主稍忘设置即失效，且与"自部署、装完即用"的定位冲突。修复必须在构造点，使正确性不依赖运行方式。

### D2 守护测试必须真起子进程

断言方式：同一份归档证据 + 同一份判分配置，在两个独立子进程中各构建一次判分输入，比较逐字节结果。

*备选*：进程内用 `sys.hash_info` 或 monkeypatch 模拟。否决 —— 同进程内 hash 种子恒定，这样的测试**永远绿**，恰好是最坏的那种测试：它声称覆盖了缺陷而实际测不到。子进程启动成本约几十毫秒，可接受；若 CI 判定过慢，用 `@pytest.mark.slow` 归入慢道而非降级为进程内断言。

### D3 `implementation_version` `"2"` → `"3"`，不新增口径字段

判分输入变了就是口径变了，沿用④ 已有的评分器版本机制即可，`verdict_drift` 天然能按它分组。

*备选*：新增 `prompt_version` 字段。否决 —— 与 `implementation_version` 语义重叠，两个版本号会互相矛盾，且给 `EvidenceBoundary` 增加一个无对照价值的维度。

### D4 把 `evidence_levels` 的隐性不变量固化为测试

审计中发现：`graders/_evidence.py:59-71` 的 `channel_levels` 用 `sorted(present, key=lambda level: -level.strength)` 对一个 **set** 排序。当前确定，因为 `EVIDENCE_STRENGTH = {"harness": 2, "runner": 1, "subject": 0}` 两两不同；一旦新增第四级且与既有某级同强度，并列项的次序就回落到 set 迭代序 —— 而 `evidence_levels` 是**落盘的判定字段**，会直接进 `EvidenceBoundary` 与跨 run 可比判定。

决定：加一条断言测试「`ObservedBy` 各级强度两两不同」，不改 `channel_levels` 本身。

*备选*：(a) 不处理，仅在文档记一笔 —— 否决，这正是本变更要消灭的那类缺陷，且成本是一行测试；(b) 给 `sorted` 加二级 key 兜底 —— 否决，那是在掩盖一个应当被拒绝的建模选择：两级同强度意味着可信度分级本身有歧义，静默打破平局比报错更糟。

### D5 审计范围与结论

对 `graders/`、`metrics/`、`trace/` 全部集合用法逐一判读，结论如下（供后来者免予重复怀疑）：

| 位置 | 用法 | 是否影响判定 | 结论 |
|---|---|---|---|
| `graders/model_based.py:158` | `list(set(...))` 插值进提示词 | **是** | **本变更修复** |
| `graders/tool_calls.py:96-97` | set 做子集/成员测试 | 否，结果与序无关 | 安全 |
| `graders/transcript.py:158` | `len(set(...))` 取基数 | 否 | 安全 |
| `graders/_evidence.py:35,42,115` | `frozenset` 做成员测试 | 否 | 安全 |
| `graders/_evidence.py:59-71` | set → `sorted(key=-strength)` | **是**（落盘字段） | 当前安全，依赖强度两两不同 → D4 固化 |
| `trace/normalize.py:75-76,96-100,139` | `frozenset` 用于 `key in ...` | 否 | 安全 |
| `trace/mapping.py:189` | `frozenset` 返回属性名集 | 否（成员测试）；枚举呈现次序不影响判定 | 安全 |
| `graders/human.py:76` | `time.time()` 作 `created_at` | 是，但时间戳本就是口径的一部分 | 预期行为 |
| `metrics/`（含轨迹注入路径） | 无集合派生内容进提示词 | 否 | 安全，已由测试钉住（tasks 1.3） |

实现期 grep（`graders/` `metrics/` `trace/`）补出的点位，一并判读以免重复怀疑：

| 位置 | 用法 | 是否影响判定 | 结论 |
|---|---|---|---|
| `graders/tool_calls.py:99-100,110-114,129-131` | `missing`/`violated`/`used_tools` 落盘清单 | 序由**配置列表**与 span 序决定，set 只用于成员测试 | 安全 |
| `graders/_evidence.py:123-125,144-146` | 对 frozenset 按 `-strength` 排序后进 explanation | **是** | 与 `channel_levels` 同一条 D4 不变量，同样被其测试覆盖 |
| `trace/normalize.py:129-134,158,230,258` | set 收名后 `sorted(masked)` / `.sort()` 落盘 | 否（已排序） | 安全 |

本变更范围外、但**同一类**的两处（`core/metrics.py`，属统计口径而非判分输入）：

| 位置 | 用法 | 后果 |
|---|---|---|
| `core/metrics.py:876` | `max(set(reasons), key=count)` 取众数 | 计数并列时，落盘的 `cost_unknown_reason` 随进程变化 |
| `core/metrics.py:358-361` | `sum(...)` 遍历 `set` 求 κ 的 `p_e` | 求和次序随进程变化，结果可有末位浮点差 |

两者都不进判分输入、不动 verdict，故本变更**不修**（修它会把统计口径改动混进一次 pure 修复里，正撞 D5 的归因约束）。留作独立判断。

## Risks / Trade-offs

- **[修复会翻转历史 run 的重评结论] →** 这正是 `implementation_version` bump 的作用：翻转被归因到"评分器版本 2→3"，而不是被误读成 judge 更换或 agent 退化。发布说明需点名"重评同一批字节可能得到不同分数，且这是修复"。
- **[现有单测可能断言过工具清单的某个具体次序] →** 该断言本身就是本缺陷的产物；随修复一并改为断言"稳定且有序"，不迁就旧断言。
- **[子进程测试在 CI 上不稳定（Windows 进程启动、临时库文件竞争）] →** 测试不依赖数据库，只在内存构造证据对象后跨进程比较字符串；避免共享临时文件。平台差异由既有的 Windows 观测道覆盖。
- **[D4 的强度唯一性测试约束未来建模] →** 这是**故意**的约束。若将来确实需要两级同强度，应先在本能力层面辩论清楚"可信度分级出现并列意味着什么"，而不是被一个 `sorted` 的二级 key 静默决定。

## Migration Plan

1. 修复 + 版本 bump + 两条新测试，同批提交（拆开会让中间态出现"输入已变而版本未变"）。
2. 对一个含多工具调用的历史 run 执行重评，用 `verdict_drift` 记录翻转数量，写入 `tasks.md` 的验证条目。
3. 回滚：本变更无数据迁移、无 schema 变更，回滚即还原 `model_based.py` 与版本常量；已按 v3 落盘的 verdict 保持不动（历史 verdict 永不覆盖），只是后续重评回到旧行为。

## Open Questions

- 重评验证选哪个历史 run：优先选 `examples/achat` 的活跑归档（真实多工具），若其 trial 数太少不足以体现翻转，再补一个 MockRunner 构造的多工具套件。两者都做不冲突，属执行期选择。
