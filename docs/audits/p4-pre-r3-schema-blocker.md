# P4_PRE-R3 Schema 阻断与最小修订提案

状态 P4_PRE_BLOCKED；HEAD a41952f8dbbcaa568ed57974f7d896b0d64b5ac2；未提交、未推送、未修改或应用 Migration。

## 原始证据

- 源码：alembic/versions/0002_m1_rag.py:72–82，embedding_profiles_identity_ux 唯一列为 provider、model_name、model_revision、dimension、distance，不含 fingerprint。
- 消费/写入：backend/app/adapters/postgres/knowledge_repository.py::process_job，按完整 fingerprint 查找新 Profile 后 INSERT；当前完整七项身份由 backend/app/domain/embedding_identity.py::EmbeddingIdentity 定义。
- 真实环境：固定 Clean-slate PostgreSQL，OID 21278，system identifier 7691227493754040358，migration 0016_parent_child_chunks。
- 必要合同反例：backend/tests/test_clean_slate_model_profiles.py::test_distinct_input_semantics_profiles_can_coexist。两个 identity 的 provider/model/UNKNOWN revision/dimension/distance 相同，input semantics 不同，因此 fingerprint 不同；实际第二次 INSERT 被 embedding_profiles_identity_ux 拒绝。
- 命令：RAG_R3_CLEAN_SLATE_TEST=1、TEMP/TMP 指向本项目 var/reports/p4-pre-r3/os-temp，执行 `.venv/Scripts/python.exe -B -m pytest backend/tests/test_clean_slate_model_profiles.py -q --tb=short -p no:cacheprovider --basetemp=var/reports/p4-pre-r3/schema-conflict-temp --junitxml=var/reports/p4-pre-r3/schema-conflict.xml`。
- 结果：4 PASS / 1 FAIL，退出码 1；psycopg.errors.UniqueViolation，constraint embedding_profiles_identity_ux。模型调用 0，全部事务回滚，24 业务表前后计数相同且最终 0。

本反例使用可选 Ollama 身份，无本地模型调用；约束对 Provider 无例外，同样阻断 SiliconFlow 的新输入语义或新 chunking identity。先前四项真实 PG 检查只覆盖不同模型/单一输入语义，并未覆盖此冲突，因此不能据其宣布代码完整通过。

## 影响

目前不能在保留诚实 UNKNOWN 模型 revision 与历史 Profile 的前提下，可靠地为同一模型建立不同输入语义/P3 参数的 Profile。直接覆盖旧 fingerprint 会使历史向量引用失去身份；将输入语义/P3 identity 填入 model_revision 会伪造供应商版本；删除旧 Profile 会破坏历史依赖。三种方式均不采用。

当前默认云 admission/职责开关关闭，所以不会为该失败消耗真实 Embedding 请求。但真实 API 续验前必须处理 Schema 冲突，不能发请求后才接受数据库写入失败。

## 待 Owner 决定的最小方案（未执行）

1. 新增 0016 之后的独立 Migration，既有 0014–0016 保持原样；只调整 embedding_profiles 的唯一键，把 fingerprint 纳入 `(provider,model_name,model_revision,dimension,distance,fingerprint)`。无需复制业务行或重建 chunk_embeddings，既有 Profile ID 与 FK 不变。
2. 迁移前对目标实例做只读重复检查和原约束核验；不得删除旧 Profile 或业务数据。对非空历史库需另行明确授权，本轮只针对固定 Clean-slate。
3. 仅在获准 Schema 后，把 Repository 的 Profile get-or-create 改为以批准的新唯一键执行原子 INSERT ... ON CONFLICT ... RETURNING，或等价的事务安全路径，防止同身份并发创建相互失败。
4. 重跑真实回滚 PG 测试：相同完整身份复用、同模型不同输入语义/不同 chunking identity 并存、不同 Provider 同维度隔离、查询拒绝错误 Profile、历史 ID/FK 不变，以及已运行的摄取/Child/引用相关定向检查。
5. 降级必须先检查是否已有按旧五列重复的新 Profile；若有则明确阻断降级，不能靠删除历史行恢复旧约束。迁移备份/恢复与真实应用授权另列，不在本轮执行。

这只是最小修订提案，不是 Owner 批准，也不是可执行 Migration。已有代码和失败证据保留；不 commit、不进入 P4。输入 tokenizer/非截断、真实 Key/有限外发、模型漂移与“依赖升级影响未独立验证”等限制继续存在。


---

## P4_PRE-R3-FIX 当前修复结果（以上 R3 阻断和失败记录保留）

代码状态 **P4_PRE_R3_CODE_PASS**。整体 **P4_PRE_BLOCKED**，不是 P4_PRE_PASS。
本节基线 HEAD `a41952f8dbbcaa568ed57974f7d896b0d64b5ac2`；执行方式 MANUALLY_SUPERVISED_TRIAL，实际 serving model/effort UNKNOWN。Owner 已授权最小 Schema 修订，并另行明确授权本次标记合成 Profile 的短暂提交及精确清理。

### Schema 与生产接线

- 新增 `alembic/versions/0017_embedding_profiles_fingerprint_identity.py`，真实 revision 为 `0017_embedding_profile_identity`（符合现有 Alembic version_num 长度），down_revision 保持 `0016_parent_child_chunks`。upgrade 先核验原五列约束、fingerprint NOT NULL 及空/NULL 指纹数据，再使用 Alembic 标准 drop/create unique constraint 增加 fingerprint；不改 ID、指纹、模型 revision、外键或 Embedding 行。downgrade 如发现旧五列重复则明确拒绝，不删除数据。
- 唯一真实迁移目标为 `rag_clean_dev_20261008t072656z_352f705b`，127.0.0.1:25438，OID 21278，system identifier 7691227493754040358。检查时无其他 client backend，24 业务表迁移前后及测试后均为 0。真实约束为 `UNIQUE (provider, model_name, model_revision, dimension, distance, fingerprint)`；fingerprint 仍 NOT NULL，chunk_embeddings Profile FK 及 P3 Parent-Child FK 定义前后相同。
- `scripts/migrate_clean_slate.py` 是专用执行环境：核验 profile 身份、真实 OID/cluster、迁移版本、空库及消费者后，通过明确覆盖的 RAG_DATABASE_URL 调用标准 Alembic；不启动服务，不复用旧库目标。仅成功验证后更新 `deploy/clean-slate/profile.json` 的 migration_revision；其余字段与 HEAD 完全相同。`scripts/with_clean_slate.py::resolve_environment` 进一步固定获准库名/OID/cluster，原 Storage、端口、P3 identity 与真实迁移版本保护保留。
- `PostgresKnowledgeRepository::_get_or_create_embedding_profile` 采用六列 `INSERT ... ON CONFLICT ... DO NOTHING RETURNING id`；仅在 READ COMMITTED 中允许执行。冲突后只重新 SELECT 一次完整六列身份，行仍不可见则明确失败，无无限重试、revision 伪造或 fingerprint 覆盖。非法/不完整身份在 SQL 前拒绝；非预期 PK 冲突保留错误。process_job 使用该方法；get_embedding_profile_id/vector_candidates 同时过滤 Provider/model/revision/dimension/distance/fingerprint，原 Scope、active-version、child-only 与 P3 identity 过滤和排序算法保留。

### 实际命令与结果

命令完整参数、白名单环境和退出码分别在 `var/reports/p4-pre-r3-fix/*-command.json`；汇总 `check-summary.json`。未实现的命令不得报告通过。

| 实际执行 | 退出码 | 实际结果与证据 |
|---|---:|---|
| `.venv/Scripts/python.exe -B scripts/migrate_clean_slate.py` | 0 | 真实 0016→0017；migration-before.json / migration-after.json / migration.log |
| `.venv/Scripts/python.exe -B scripts/migrate_clean_slate.py --reapply` | 0 | 标准 Alembic 已在 0017 时无重复 DDL/数据；migration-reapply-*.json |
| `run_checks.py` 中 targeted-final 的 pytest 命令 | 0 | 351 PASS / 0 FAIL / 0 SKIP；targeted-final.xml |
| `run_checks.py` 中 db-final2 的 pytest 命令（两个显式 DB opt-in=1） | 0 | 26 PASS / 0 FAIL / 0 SKIP；db-final2.xml，真实 PostgreSQL/pgvector，模型 SIMULATED |
| `run_checks.py` 中 broad-final 的 pytest 命令 | 1 | 1233 PASS / 9 FAIL / 20 ERROR / 127 SKIP；broad-final.xml；29 项原失败逐项状态/原因相同，仅归一化源码行号，没有新增失败 |
| `.venv/Scripts/python.exe -B var/reports/p4-pre-r3-fix/run_checks.py` | 0 | 验证上述退出码、29 项对照及测试期间源码哈希无漂移；不把 broad exit 1 改成全绿 |

真实 DB 测试包含原 `test_distinct_input_semantics_profiles_can_coexist`、相同身份 ID 复用、不同语义/不同 P3 identity/不同 Provider/模型并存、错误 Profile 不返回其他向量空间、完整输入/来源 quote/child-only、现存合成 Profile ID/FK/Embedding 在真实 0017 DDL 前后保持、非法身份、非 READ COMMITTED 拒绝及非预期 PK 冲突不重试。

并发测试使用独立真实连接和不同 pg_backend_pid。相同身份观察到 pg_blocking_pids 冲突等待，获胜提交后第二连接返回同一 ID，最终已提交 Profile 数为 1；不同身份的两次 INSERT 均在任一提交前完成，最终保留两个不同 ID。仅这两项按 Owner 追加授权短暂提交，随后按本次唯一 SIMULATED model marker、ID 与六列身份精确删除合成 Profile：最终轮共清理 3 行，非测试行删除 0，库最终为空。其他测试全部 rollback。具体 PID、ID、fingerprint、提交计数、清理 ID 和最终 0 见 committed-concurrency-same.json / committed-concurrency-distinct.json；早轮同类证据保留于 round1-*，不得把授权清理描述为全回滚。

### 失败记录、保护及限制

原始 Schema RED（4 PASS / 1 FAIL）仍在 `var/reports/p4-pre-r3/schema-conflict.xml`。本轮首轮 350 PASS / 1 FAIL 因 SIMULATED SQLRecorder 未实现新的连接隔离级别及 INSERT RETURNING 响应，保存 targeted-first.xml/log/command 与 first-tested-source-snapshot.json；修复仅补 Double 协议并新增 ON CONFLICT 断言，原 P3 来源、完整内容、数值、引用、父子和 child-only 断言均未删除。未修改 P3 Chunking 生产算法。

原四项 Caption 历史目录比较保持 **NOT RUN**。未改 Caption 实现，复用此前同哈希源码的 35 PASS / 4 DESELECTED 证据；不把 DESELECTED 算 PASS。历史 Migration 0001–0016 的 raw SHA 与入场一致；三份 frozen PDF 二进制 fixture 与 HEAD 字节完全一致，其余 fixture 与前轮 raw SHA 一致、与 HEAD 仅允许既有 EOL 差异（provenance.json 的 CRLF 已存在，并非本轮漂移）。gold.json 仍 18157 字节、SHA-256 d44ff365b77800fab83b4604b26a53f9152fa8d810851e52f8cf93aae8836f79。原 27 个暂存文件入场 raw/index 哈希全部一致，原内容和失败证据保留；本轮仅追加获准 Schema/Repository/测试/专用 helper/配置与报告变更。

旧业务数据库未连接、未读取、未写入，故本轮其前后行数 **NOT RUN**，不能声称已独立排除其他消费者写入。公开语料未读写，无新增全量目录盘点；仓库历史资产逐字节保护结果在 protected_hashes。Clean-slate Storage 配置保持不变，测试 CAS 只写仓库临时目录。未调用真实模型/API，未下载模型/tokenizer，未执行 P4 正式检索链或重建业务索引。

**依赖升级影响未独立验证**。真实 SiliconFlow Embedding、模型对应 tokenizer/不截断输入保障、实际模型 pgvector 端到端验证仍 NOT RUN；UNKNOWN revision 的静默模型漂移、真实结算及原 R3 限制继续保留。代码通过不解除这些上线前阻断项。

最终 whitelist、工作树/暂存 blob SHA-256、完整 diff、默认 diff --cached --check 实际结果和独立本地 commit 回执，见本轮 final-manifest.json、git-checks.json、full.diff、repair.diff、commit-receipt.json。只有上述检查实际通过后才执行用户指定提交；不 Push、Merge、Tag，不进入 P4。
