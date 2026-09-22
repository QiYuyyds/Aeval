# 判分输入确定性 — 验收证据的复现入口

变更 `make-judge-prompt-deterministic` 的第 6 组任务要求"在一个含多工具调用的 run 上
真重评，量出评分器 v2→v3 翻了多少判"。那两个数由下面两个脚本产出，**都不需要 LLM 凭证**。
归档记录本身写在变更的 `tasks.md` 里；这里放的是**能按同样方法重跑出同样的数**的入口。

## 两个脚本分别答什么问题

| 脚本 | 答的问题 | 能否进 CI |
|---|---|---|
| `measure_version_flip.py` | 换代让多少 trial 的**判分输入**变了、翻了多少 verdict、翻判归因在哪根口径轴上 | 否（一次性测量，且要读 git 历史） |
| `replay_archived_prompts.py` | 在**真实归档**的 trial 上，换代改写了多少条判分输入（真实翻转数的上界） | 否（要宿主库路径 + 越界读取授权） |

跨进程逐字节一致那条断言**不在这里** —— 它已经是常驻测试
（`packages/agent-eval/tests/test_prompt_determinism.py`），比人工重跑更可靠。

## 运行

```bash
# 换代前的实现 = 修复落地前的那个 commit（其 model_based.py 里仍是 list(set(...))）
python examples/prompt-determinism/measure_version_flip.py --rev 1b01aa0^

python examples/prompt-determinism/replay_archived_prompts.py \
    --db /path/to/aeval.db --suite /path/to/suite.yaml --rev 1b01aa0^
```

在仓库根执行（脚本要用 `git show <rev>:...` 取换代前原文）。两个脚本都会**拒绝**传入
修复后的 rev —— 那只会得到"输入没变、0 差异"，而 0 差异不能当作"修复无影响"的证据。

## 两条已知的解释边界

- `measure_version_flip.py` 里的 judge 是**确定性的呈现序敏感替身**（清单里第一个工具名
  `<'f'` 才给 `completeness` 1.0）。它证明"输入变了会让判分跟着变、且归因只落在判分实现
  版本这一根轴"，**不是**任何真实模型的翻转率。真实翻转率要等一把能用的 judge 凭证
  （宿主四把候选截至 2026-09-22 全为 401/402）。
- 替身判据下的翻转数本身随进程浮动（实测 10 次里 9 次 6/6、1 次 0/6）：换代前那份清单的
  次序就是随进程浮动的，所以"翻了几判"在旧实现下不是一个定值。这恰好是本变更要修的缺陷
  的另一面，**不要**把某一次的数字当结论看。
- `replay_archived_prompts.py` 只读打开库（`mode=ro&immutable=1`）且只打印聚合量，不打印
  任何 trial 正文。
