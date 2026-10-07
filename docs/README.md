# 文档索引

先读 [当前开发状态](SOURCE-STATE.md)、[项目执行约束](../AGENTS.md) 和 [已知问题](KNOWN-ISSUES.md)。本索引新增于2026-10-03整理阶段，不改变需求、历史证据或验收结论。

## 开发与验证

- [测试入口与副作用说明](TESTING.md)：解释器/工作树确认、临时插件选项和离线测试边界。
- [产物保护规范](ARTIFACTS.md)：源码、运行数据、冻结结果与每轮任务产物的区别。
- [历史执行进度](../progress.md)、[历史任务计划](../task_plan.md)：保留各阶段记录，不能替代当前状态。
- [Windows原生运行与备份说明](windows-native-release.md)、[Local-first runbook](reviews/local-first-v1-runbook.md)：运行/发布有独立授权和验收条件，不直接照抄旧重建命令。

## 决策、设计与契约

- [ADR索引](adr/README.md)：复用已有决策链。
- [Local-first RAG设计](design/local-first-rag-v1.md)、[资源路由设计](design/local-first-resource-routing.md)。
- [检索冻结决策](decisions/v1-retrieval-decision.md)、[LangChain Smart ADR](adr/ADR-003-langchain-smart-agent.md)。
- [契约索引](../contracts/README.md)、[评测指标协议](evaluation-metric-protocol.md)、[评测中心运维](evaluation-center-operations.md)。
- [PyMuPDF许可说明](NOTICE-PyMuPDF.md)：保留已有归属文件。

## 阶段证据：保留原文，按阶段阅读

| 报告 | 适用范围 |
|---|---|
| [旧V1交付记录](reports/v1-delivery-report.md) | 当时执行结果；其中旧命令和PASS不代表当前源码或当前发布验收 |
| [2026-09-30 Local-first最终报告](reviews/local-first-v1-final-report.md) | 检索/真实资料流程收敛，报告明确不是V1发布验收 |
| [V1 Quality Hardening报告](reviews/local-first-v1-quality-hardening-report.md) | 独立候选与质量限制；不得改写为整体业务质量提升 |
| [检索优化Round1](reviews/2026-09-30-rag-retrieval-optimization-round1.md) | 固定公开检索实验，不等同最终答案质量 |
| [真实资料质量基线](reviews/real-material-quality-baseline-20260930.md) | 冻结输入与基线；原题库、金标准和历史版本保留 |
| [旧current-state审查](reviews/rag-current-state.md) | 历史实现状态，不能覆盖本页记录的新未验证补丁 |

`docs/superpowers/plans`、`docs/superpowers/specs` 和本地计划目录是阶段计划，不在本次整理中合并、移动或删除。
