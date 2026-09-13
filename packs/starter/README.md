# starter pack

Aeval 随 wheel 分发的内置示例套件（`eval-suite run demo` 即运行本 pack）。

- **离线可跑**：确定性 code / artifact 判据，零 LLM、零网络、零凭据；未显式
  传 `--runner` 时自动接内置 `MockAgentRunner`。
- **pack 结构**：`suite.yaml` + `manifest.json`（pack_name / suite_version /
  逐文件 sha256）。manifest 由 `agent_eval.core.packaging.write_manifest`
  生成，加载时逐文件校验，任何改动都会被拒绝并点名文件。

## 同源纪律

仓库 `packs/starter/` 与包内 `agent_eval/packs/starter/` 必须逐字节一致，
`tests/test_packaging.py` 会比较两者，漂移即测试失败。

修改本 pack 的流程：

1. 改 `suite.yaml`（或本 README）；
2. 重新生成 manifest 并同步两份拷贝：

   ```bash
   cd packages/agent-eval
   python -c "from pathlib import Path; from agent_eval.core.packaging import write_manifest; write_manifest(Path('src/agent_eval/packs/starter'), pack_name='starter')"
   cp src/agent_eval/packs/starter/{suite.yaml,manifest.json,README.md} ../../packs/starter/
   ```

3. 跑 `eval-suite run demo` 与测试确认。

想把这套内容当作自己套件的起点？把 `suite.yaml` 复制出去改，打包发布见
`docs/release-checklist.md`（公开发布前检查单）。
