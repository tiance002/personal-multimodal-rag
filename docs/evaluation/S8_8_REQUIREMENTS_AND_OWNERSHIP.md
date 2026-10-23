# S8.8 产品合同与文件责任

基线：`f3a541ef1cb29d16f9dd81cacfaa207d894f3e5c`。本轮独立分支 `codex/s8-8-implementation-20261023`。

Quick/Smart 是最终生成的显式思考开关，与 Cheap/Expensive 模型选择正交。Smart 复用普通 RAG，不接旧 Agent 501 路径。前端只开放模式和最大输出 Token，以及恢复默认/有效值说明；不实现 Prompt 编辑器。VLM 设置独立保存。系统 Prompt 候选仅用于有版本的受控比较。

| 工作流 | 负责人 | 独占写入范围 | 关键验收 |
|---|---|---|---|
| A | Sol xhigh | HTTP/configuration/runtime/service/models/generation coordinator、前端；共享接口统一编辑 | 请求到有限额度与 Provider 参数、持久化/升级、浏览器 |
| B | Astra high | image 模块/图片处理与任务存储；query 固定澄清；collector；共享修改交 A 集成 | 非完整 Caption 不入成功索引；旧 OCR 保留；局部重处理；固定回复及取消 |
| C | Sol xhigh | retrieval（除 query）与定向 replay/tests | 17 例断点、63 题指标、确定性错误最小修复 |
| D/集成 | 主代理 | 新 prompt_candidates/shadow 模块、对应 tests、集成报告与验收 manifest | 影子不改 dispatch，未知保持未知，Prompt 单因素比较 |

ECS 构建、部署及真实 Provider 请求由主代理单一执行。禁止并行代理修改生产或发起项目模型调用。当前本轮新收费授权未提供；旧 Grant 关闭，继续完成全部离线实施，冻结版本与输入后集中申请。

历史 S8.7 原始结果保持；公开摘要中“3 道救回”包含原 PARTIAL 情况，不声称均为完全错误到完全正确。本轮私有问答和 Context 留在受控本地目录。
