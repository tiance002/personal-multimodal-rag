# V1 Quality Hardening 实施计划

目标：保留 V1 62.5% 原始 Baseline，以通用管线修复建立独立 V1.1 Candidate。

架构：共享 EvidenceService 负责生成后审核、审计与原文降级；Quick 与只读 Agent 使用同一机制。确定性清单与预算不读取评测 Reference，不增加模型调用。审计正文仅在忽略的本地 var/storage 中保存。

技术：现有 Python、LangChain、Ollama、PostgreSQL，不新增依赖或模型。

## 约束

- Frozen Reference SHA：7b7468dcdde96fe17bfe35d4acdfd070b7ba299082af656d257c49716f856291。
- 不修改检索参数、题目、源资料、V1 结果与评分；不执行公开 benchmark 或 Cloud。
- 串行运行同一32题；首轮暴露P1后保留全部结果，经负责人明确允许再跑一次最终真实回归，报告机器 Judge 与部分 Human Overlay 的不同身份。
- 未实现的命令不得报告通过。

## 步骤

- [x] 保存输入 hash 清单、独立 8 题 Human Overlay。
- [x] 写失败测试：多意图保守路由/覆盖，预算，引用去重/映射，审计/降级，截断。
- [x] application/answer_hardening.py：清单、预算、引用映射、保守原文提取与显式矛盾检测。
- [x] adapters/answer_audit.py：本地独立审计文件；EvidenceService 接入并共享 Quick/Agent。
- [x] citations.py 保持 chunk/version 的稳定身份；Ollama 保存截断 candidate 并限制预算。
- [x] 执行受影响单元/集成/范围及外发边界测试，记录真实退出码。
- [x] 同一 32 题单次串行回归；独立 V1.1 评分与候选路由，不继承旧答案的人审结论。
- [x] 输出中文报告，核对不可变 hash，复制可打开的交付文件到当前工作区后停止。

## 产品价值/过拟合门

审计让任意拒答可追责；原文降级避免丢弃直接证据；预算与清单适用于明显复合问题；稳定引用身份使任意重复证据可回读。所有机制在没有这 32 题时也成立。
