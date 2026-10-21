# S8.5 多轮对话检索与受控多跳检索

状态：阶段 0 源码核对完成，阶段 1–4 实施中。真实模型/ECS 本轮尚未执行。

## 基线与阶段 0

实际初始 HEAD 为 `main@0606063`，其尚不包含 `backend/weknora_port`。隔离分支 `codex/s8-5-conversation-bridge-20261021` 从它建立，合入已提交 S8.4 分支 `9931ffe`，集成提交 `7e5fb4a`。两个 ECS skill add/add 冲突保留当前 main 的增补案例；未改历史 S8.4 输入、Gold、收据、报告。主工作区原有 `.serena/project.yml` 保留。

实际调用链：服务端准入 → `prepare_turn()` 读取历史/记忆 → `understand_query()` → `resolve_intent()` → Search → Rerank → Merge → TopK → 可选来源扩展/Bridge → Context → Generation → 引用注册/持久化/SSE。恢复请求回放已有运行，跳过 `prepare_turn()`。

| 检查点 | 已实现 | 缺陷 | 缺少验证 |
|---|---|---|---|
| Session 历史 | `prepare_turn` 调用 `OrdinaryHistoryService.load`，SQL 和 `process_history` 均按 tenant/subject/session 过滤，按时间取 max_rounds | 未排除未完成、取消/错误和当前 request；异常大范围吞掉权限/超时 | 本轮隔离真实 PG/多用户测试未运行 |
| Query Understand | 收到历史并保留 `state.query=q0`，复用一个已有改写调用 | 仅非空字符串校验；不能阻止条件遗漏/歧义实体选择 | 当前模型在 S8.5 新合同上的真实输出未验证 |
| 检索查询 | Embedding、关键词和 Rerank 已用 `rewrite_query` | 需增加失败回退轨迹及对有效查询的断言 | 现有真实记录不替代新合同验收 |
| 意图保护 | 有知识库/来源约束规则、附件前提 | 任意有历史的 `follow_up` 可绕过检索；事实性追问被误放行 | 需模拟模型误分类回归 |
| Merge / Context | 当轮证据注册和来源映射已具备 | Merge 会注入历史引用内容，可能替代当前检索 | 需证明旧引用不进入最终 Context |
| Bridge | 默认 off、显式模式执行；单 Cheap/单有界 Rerank/当前原文回读 | 旧偏移合同、对象截断/时间归属/嵌套地点误判、并列谓词误拒、代词不全 | S8.4 真实仅 2 次 Cheap 拒绝，0 BGE，不能认作链成功 |
| 运行账本 | 双侧结算和原始收据保存 | `validate_settlements` 缺执行侧键唯一性及键集合相等检查 | 新反例需先 RED 再 GREEN |
| SSE 恢复 | replay_only / 已有 active 运行避免重入；持久化事件回放 | 暂未发现需重写的缺陷 | 本轮离线恢复断言待执行；不宣称浏览器真实验证 |

阶段 0 只读检查的相关模块包括 service、conversation/history/types/configuration、retrieval/query/search/rerank/pipeline/merge/bridge_rescue/context、generation/context 和 storage/memory/turns/history。未重构或新增主链。

## 阶段记录

后续逐阶段记录修改、RED→GREEN、命令退出码、离线与真实结果。未实现的命令不得报告通过。
