# 当前开发状态与证据入口

状态日期：2026-10-03。本页是整理时的快照，开发与验证仍暂停；不是发布或业务质量验收报告。

| 对象 | 本轮确认的身份与边界 |
|---|---|
| 指定开发工作树 | 工作树标识 `rag-retrieval-opt-round1`；机器路径见独立任务交付说明，不在仓库记录 |
| 分支与 HEAD | `codex/local-first-rag-v1-20260930`；`52d770e8418bb040c372bf8d9014381ed68bc160` |
| 未提交工作 | 整理前盘点为44个 tracked 修改项、85个未跟踪状态项；包含目录项，不是永久统计，也不能当作垃圾清单 |
| 其他工作树与环境 | 其他已注册工作树和既有解释器环境不等于本工作树；运行服务的实际源码身份本轮未核查 |
| 本工作树 `.venv` | 2026-10-03 已创建隔离环境：Python 3.13.0，不继承系统包；官方 PyPI 项目 `.[dev]` 安装、`pip check` 及第三方模块导入核查通过；应用测试尚未运行 |
| 教学 snapshot | 不作为当前开发或运行源码；其独立身份与位置本轮未核实，不更新或搬移 |

当前源码身份还包括未提交与未跟踪内容，不能只用 HEAD 表示。根 [progress.md](../progress.md)、[task_plan.md](../task_plan.md) 和阶段报告保留历史状态，本页不覆盖它们。

## 已落地但未验证的最小补丁

已写入 [retrieval.py](../backend/app/application/retrieval.py)、[knowledge_gateway.py](../backend/app/application/knowledge_gateway.py)、[config.py](../backend/app/config.py)、[bootstrap.py](../backend/app/bootstrap.py) 和新增 [test_context_pool_policy.py](../backend/tests/test_context_pool_policy.py)。四个源文件原先已有未提交改动；本次增量备份、diff和哈希清单保存在独立任务产物中。

补丁拆开候选池与最终上下文数量；新开关默认关闭，本任务未启用运行配置。公开检索 Top5、8000字符上限及原引用校验保留；这些兼容性要求仍需应用测试确认。

- RED曾在正确工作树中正常结束，按预期暴露当时缺少的新参数；随后写入补丁。
- GREEN、相关回归和 aggregate均未运行。历史“608 passed”记录只属于当时的源码与环境，不能作为本补丁回归证据。
- 既有冻结回放是 expected-chunk ID 召回观察，不是修改后真实问答或语义引用支持验证。
- 开发保持暂停，先完成导航整理审阅，再决定验证与后续开发顺序。

## 仍需保留的限制

历史业务质量记录未证明整体质量验收通过；当前新补丁的正确率、引用支持、拒答、端到端延迟和实际用量尚未测量。既有云端最终生成器结果不能称为端到端对照。

平台曾拒绝的 OID 与预算清理动作仍暂停，不能借整理重试。手工问答、资料导入及辅助模型的 token 用量尚未核实；未知用量不按零处理，旧累计不能视为当前可用余额。本轮不读取或更新账本。

旧 `output.json` / `archive` 没有副作用发生前的哈希基线，完全未变状态为 UNKNOWN。本轮不移动、删除或修复它们。

下一步见 [已知问题](KNOWN-ISSUES.md)、[测试说明](TESTING.md) 和 [产物保护规范](ARTIFACTS.md)。
