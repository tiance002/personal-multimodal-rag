# 共享 authority 装配与 Token 门：本地实施检查点

基线 `551c5f050135157be651146f19115cd6ecca078b`；独立 E 盘工作树与原分支继续，未写原工作区。依据独立 `IMPLEMENTATION_REVIEW.md` 的三项修正及 Owner `Sentinel_42a8526687e481919bee0193a7b3d1aa`。本提交完成**共享装配的合成正常闭环及相关 Token 安全边界**；不宣称真实生产装配全部完成或准许外发。

## 1. 实际改动

- `authority.py` + `development.assemble`：API/worker 共用只读配置 loader/factory、原 `SendAuthorization`/`ServerPermissionResolver` 与 Token authority。配置不含价格、金额或真实 key；`--authority-config` 仅服务器 CLI 参数，HTTP 任意覆盖被拒绝。默认未配置 ready503/无调用；synthetic 只能使用代码内 MockTransport、fixture.invalid 与支持的固定协议，不能切到真实 HTTP。
- `entrypoint.py` / `worker.py`：两个标准 main 使用同一模块/配置 SHA。Token readyz 验证实际 callback 类型、主体、有效期与月份，不要求假 CostAuthority 或非空占位对象。
- `storage/send_gate.py` + `token_budget.py`：沿用一个 P1 外层门，新增显式 Token 存储分支。money 与 tokens 同一个调用锁，调用锁→月锁→row，跨两账本和统一 slot 记录检查所有旧身份，released 也不能重发。admission 拒绝外层未提交事务，采用 READ COMMITTED 锁后新快照，返回前已 commit。
- `014_token_authority.sql` / manifest / `token_migrate.py`：新增显式 Token 月表/调用表及单位共用 slot 身份表。未把 microunits 换成 Token，未删除/改写历史金额表。启动只检查 schema，迁移是独立显式操作；empty-new initialize 纳入新 migration。实际仅在既有获准隔离 PG 应用。
- `source_facts.py`：不可变 Source/Bound/Slot 类型；恢复 key 来自 logical operation、stage、batch、allowed attempt，**不包含模型/config/wire**。这些只作首次指纹绑定；answer 的 Cheap/Expensive/baseline 角色及单/多 batch 第0批规范化为同一槽。配置/模型变化不能为旧 UNKNOWN 产生新恢复身份。
- `models/usage_facts.py` / types/usage/native_response/provider/transport：在旧归一化前捕获原始数值字段存在性、路径、valid、reported total 与 derived total；缺字段为 null/UNKNOWN，明确0保留0，cache 不再次加到 total。流快照不累加，已知下界不能被后续较小/缺失快照抹掉。Mock usage 标 SIMULATED，经过原 transport finally→LeasedSendGate→P1.finish 实际持久化。
- UNKNOWN 占用：保存原 reserved input/output，另存 known lower / held；`held=max(original total reservation,known actual lower)`。完整一致的实际 total 才 settle；缺 cache breakdown 不抹去已知 total。总量或任一分量越界，UNKNOWN/held/原始usage 与 month block 同事务提交，commit 后才抛错。后续缺 usage 的 finish 也不会清掉先前原始事实。
- `documents.py` / `document_profile.py` / `development.py`：只给当前 embedder 绑定持久 source-version/阶段操作身份，重试不能靠随机 UUID 新发送；没有改切块、检索排序或 summary 内容/父链接。`memory_runtime.py` / runtime：item embedding 有稳定身份；Token 模式下没有可靠 operation_id 的 memory 模型调用拒绝恢复发送。旧非 Token 模式保持现有行为。

合成验证中所有角色共用一个明确的 canonical `deepseek` fixture 桶、月限 1,000,000，避免角色/provider 别名拆额。这只是全新 synthetic account 的更保守共享额度，**不是**声明 SiliconFlow 实际消耗属于真实 DeepSeek 账户；真实账户映射尚未启用。

## 2. 实际命令、退出码及证据

所有命令 cwd 为独立工作树，前缀 `E:\RAG quention\.venv\Scripts\python.exe -B`。表中 launcher 为 `docs/weknora-port/evidence/AUTHORITY_ASSEMBLY_20261015/run-check.py`；完整 argv、E 盘临时路径、stdout/stderr、exitcode/JUnit 逐次保存，禁止覆盖旧标签。

| launcher 参数 | exit | 实际结果 |
|---|---:|---|
| `focused-final tests/weknora_port/test_authority_usage.py tests/weknora_port/test_send_gate.py tests/weknora_port/test_provider_usage.py tests/weknora_port/test_provider_transport.py tests/weknora_port/test_transport_occupancy.py tests/weknora_port/test_development_entry.py tests/weknora_port/test_document_profile.py tests/weknora_port/test_documents.py tests/weknora_port/test_native_protocols.py` | 0 | 113 passed；该范围完成后仅对最后原始事实保留/slot 指纹变化作以下定向复测 |
| `snapshot-offline tests/weknora_port/test_authority_usage.py tests/weknora_port/test_provider_transport.py tests/weknora_port/test_transport_occupancy.py` | 0 | 36 passed |
| `snapshot-isolated --pg-env tests/weknora_port/test_authority_cli_pg.py tests/weknora_port/test_token_authority_pg.py` | 0 | 8 passed；真实 CLI/PG/Redis、闭合模拟模型 |
| `retained-facts-final --pg-env tests/weknora_port/test_token_authority_pg.py` | 0 | 最终 Token 存储源码 7 passed；包括后续 missing usage 不抹去旧事实 |
| `cli-loopback-final --pg-env tests/weknora_port/test_authority_cli_pg.py` | 0 | 最终源码真实标准 CLI 1 passed；loopback HTTP trust_env=False |

正常入口不依赖 tests 中的 runtime_factory 注入：测试用 subprocess 启动 `python -B -m backend.weknora_port.entrypoint --port <fresh-loopback-port> --authority-config <E:synthetic-config>` 和独立 `python -B -m backend.weknora_port.worker --once --authority-config <same-config>`，实际 argv/根目录保存在 `cli-loopback-final.pg.json`。API 完成真实 HTTP 上传→解析/索引→检索/模型 mock→回答/引用 HTTP 回读；独立 worker 完成持久图片任务。14 个正常物理调用全部以实际 mock usage settle、字段标 SIMULATED；worker 两次进程退出码均0，第二次空队列不发新调用。

故障注入在闭合 handler 的明确标记处挂起调用，观察到预留 commit 后强制终止**本测试创建**的 API 子进程。重启同一配置，重复 POST 只重放，不增 claim、不重发；最终保留一行 reserved/outcome UNKNOWN、held **11582 Token**，actual 未知。**该数字是预算预留，不是实际消耗**。API 强制终止退出码为 Windows 1，不能称正常退出；测试/worker 退出码0与这些被故意杀死的 API 进程明确区分。10 个进程日志原样复制到 `cli-final-process-logs/`，没有动已有服务。

`retained-facts-final.pg.token-cases.jsonl` 保存7个真实 PG 分支回执：预留100/已知下界150→held150且月阻断；分量95>input bound90、总预留100→held100/UNKNOWN/block；跨单位竞争恰一个winner；两连接抢月额恰一个成功；released/换角色模型重投拒绝；月锁等待期间撤销同意后0新call；取消、deadline、缺usage与新连接恢复保护。测试产生的 UNKNOWN/预留行全部保留，未释放。

保留的失败：`cli-first` exit1 是测试错误使用整数revision（API要求字符串）；`cli-fault-final` exit1 是测试先等待故意挂起的完整SSE正文导致读超时（同次7个PG边界通过）。修正测试消费为 stream headers 后 `cli-fault-fixed` 和最终命令通过；不把失败当PASS。其他开发标签均保留，不重复计作独立验收。

## 3. 验证结论及当前未完成项

共享标准入口**合成路径**：`IMPLEMENTED / OFFLINE_PASS / ISOLATED_INTEGRATION_PASS`，真实供应商请求0。Provider 旧规范化/请求字段、RAG 算法与 P5 阈值/升级开关未改；新 Token 语义为 Owner 最新安全包装，未为它伪造 Go 业务对照或供应商实测。没有重跑已接受的检索/图片全量回归、浏览器或重复独立审查。

**不能宣称生产装配 DONE**：`approved` 模式当前明确报 `APPROVED_METER_UNIMPLEMENTED`。真实完整协议/模态计量 recipe 和生产 SourceFacts 的逐来源组装/核验接线尚未实现；目前 whole-wire synthetic slice 只是闭合 fixture 的来源声明，不能当作真实 history/memory/evidence/image 来源证明。这些是仍需离线实现的源码工作，不能混说“仅等 Owner 填配置”。缺源 manual memory 及缺稳定 operation 的辅助调用维持拒绝。

真实启用另需 Owner 的允许资料/KB/global/有效期、模型与端点/可信计量资产，以及 DeepSeek UTC月100万Token的真实 carry-in、历史 UNKNOWN可靠上界、共享账户其他消费者覆盖；均未核实、未填0。本轮没有创建真实权限或凭据。旧版本进程不具新共用调用锁，真实切换必须统一版本/操作边界，不能宣称本提交能强迫未升级进程遵守新锁。

`REAL_PROVIDER_PASS / CUTOVER_PASS` 均 NOT RUN，生产真实模式 BLOCKED；尚未完整实现项如上。没有 push/tag/release/deploy，没有旧业务库连接、旧 UNKNOWN 清除或金额估算指标。必要本地费用旧协议仅用于跨单位 synthetic 拒绝测试，不作为新评测费用输出。

## 4. 保护、源码绑定和下一步

每个连接仍核验隔离 PG：127.0.0.1:56423、`weknora_python_port_20261013`、物理标识 `7696088403504697382`；Redis 127.0.0.1:56424/db15、实际run_id、独立前缀。014只新增表/版本记录；测试仅新建 tenant/account/KB/document/source。没有启动/停止 Docker 容器或更改已有配置/卷。没有采集本轮开始前旧表全量 hash，故不虚报逐行历史内容对照证明。

`.serena/project.yml` 和四个受保护文件的既有哈希逐字节不变，未暂存；原工作区不写入。`source-before-delivery.json`、`source-binding.json` 绑定实际测试源码/新文件/Git blobs和所有证据；原始日志保留字节，EOL规范化与blob hash分列。回滚只 revert 本地代码，不 drop 新表或删除测试/UNKNOWN 数据。历史浏览器缺同期源码 hash 的限制没有被本次证据追溯修补。

下一步可交 6luna 做精确固定验证，见 [AUTHORITY_FIXED_VERIFICATION_20261015.md](AUTHORITY_FIXED_VERIFICATION_20261015.md)。生产来源与可靠meter仍属后续离线实现范围；不需要新审批平台或真实调用才能继续写代码。未把该遗留自行批准延期或宣布整体Goal验收。
