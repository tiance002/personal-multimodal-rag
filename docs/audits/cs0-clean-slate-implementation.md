# CS0-R1 — 当前完成状态（2026-10-08，Asia/Shanghai）

**CS0_PASS / DELETE_APPROVAL_REQUIRED**。本节替代下方旧 CS0_BLOCKED 的当前状态；旧报告原文和外部失败证据完整保留。旧数据物理删除数量 **0**，没有获准删除的对象。

基线 HEAD `3262aede955536d473c5edb1750b9eff4d30fcfc`；分支 `codex/local-first-rag-v1-20260930`。续做入场只存在两份已知 CS0 未追踪报告，其哈希与上轮一致。原有 491 个追踪文件哈希保持；未重新开展全量资源发现。最终本地提交 SHA 见项目外 commit-receipt，不向报告写自引用 SHA。

## 实例身份与消费者

Docker Engine 在获授权主机通道实际可读；普通沙箱访问 named pipe 被拒绝，不代表 Engine 停止。按正常审批提升执行只读元数据命令，没有切换 Docker context 或绕过拒绝。`docker ps -a --format '{{json .}}'` 主机执行 exit 0。

唯一恢复的容器为 `raglocalfirst0930-db-1`，ID `5c9a6eacb99420983bc1727e0427f6cc8b2f2adf242f6ae12b8efaa531724a43`，image pgvector/pgvector:pg16，绑定 loopback:25438。Compose project/working_dir 标签指向本项目当前工作树；volume `raglocalfirst0930_rag-db` 只有此容器挂载。同项目 API 停止，启动前后 SQL 观察没有其他客户端消费者。实例 PostgreSQL 16.15，system_identifier `7691227493754040358`，容器 Unix socket 与主机 TCP 查询一致，不在 recovery。

只对这一个已确认的本项目容器执行 docker start，没有重建旧容器或停止/重启其他服务。所有其他容器的 ID、状态、端口和 mounts 前后一致；公开评测 clone 及原评测容器未启动、未连接。这里只声明 Docker 可见的消费者，不把外部并发锁或任意未知进程说成已强制控制。执行方式 MANUALLY_SUPERVISED_TRIAL；实际 serving model/effort、token/cost UNKNOWN，未派发 Worker。

## 新数据库、迁移与空数据

- 新库 `rag_clean_dev_20261008t072656z_352f705b`，host loopback，port 25438，role rag，OID **21278**，system_identifier `7691227493754040358`。
- 创建前实际 pg_database 查询确认不存在；从 template0 新建，创建后 public 表为 0。未复用、克隆或清空旧业务库。
- 在线命令 `.venv/Scripts/python.exe -B -m alembic upgrade head` 实际 exit **0**；连接只指向新库，完整 0001–0016 链执行，日志明确包含 0014_version_source_metadata、0015_processing_manifest、0016_parent_child_chunks。当前实际版本 **0016_parent_child_chunks**。
- information_schema/pg_constraint/pg_indexes 实测 context_header、parent_id、chunk_role、index_identity、版本 metadata/processing manifest 列，Parent-Child 同版本 FK、两个角色 Check、parent index 和 Embedding Profile identity/FK 存在；vector extension 存在。没有修改任何 migration。
- 6 项真实 PostgreSQL 约束探针 PASS：同版本 parent-child 接受；跨版本父引用 23503；无效角色 23514；parent 再引用 parent 23514；相同 Profile identity 23505；不同 revision 可共存。探针只用明确标注的合成行，外层事务 ROLLBACK，数据库不是模拟器；未向业务库写入测试行，没有 DELETE/TRUNCATE。
- 最终 24 张业务表全部 0 行：KB、Document、Version、Chunk、Embedding、Conversation、Answer Evidence 及资产、任务、图谱、model_calls 等均为空，仅 alembic_version 有 1 行。完整逐表计数见 empty-database-counts.json。

## 新 Storage 与明确开发配置

新 Storage 为 `D:\RAG-CleanSlate\cs0_20261008t072656z_352f705b\storage`，创建前不存在，与旧 CAS 和 D:/RAG-Public-Bench 分离。仅存在空 objects/tmp 子目录，没有复制上传文件、旧 Chunk、Embedding、Gold 或任何公开原始资料。

`deploy/clean-slate/profile.json` 保存非敏感本机新库身份、OID/cluster、Storage、迁移版本、P3 index identity 和公开基准目录。`scripts/with_clean_slate.py` 是后续 P4–P7 的明确本地开发入口，默认只作真实只读检查：

```powershell
.venv/Scripts/python.exe -B scripts/with_clean_slate.py
```

该命令实际 exit **0**，不会启动服务或调用模型。它覆盖继承的旧 RAG_DATABASE_URL/Storage，拒绝旧库名、错误端口、旧 Storage、改变的 P3 identity，并在连接后检查实际库名/OID/cluster/迁移版本。8 项 profile 检查 PASS，其中 3 项真实 PostgreSQL 只读、5 项配置断言。cloud、fallback、Langfuse、rewrite/rerank、local query/answer 和 inline ingestion 默认关闭；不把 profile 配置当模型容量授权。

后续获得相应阶段授权后，可显式使用 `--mode api` / `--mode worker` 启动同一新配置。本轮两个启动模式 **NOT RUN**。仓库原 Settings/Compose 默认和既有服务环境未改；直接旧命令仍可能指向旧配置，因此 P4–P7 必须使用这个新入口，不得复用旧业务入口。用户私下提供 PGPASSWORD 时仅在内存覆盖项目公开开发默认凭据；profile/报告均不存储密码或完整 DSN，不读 .env、容器 Env 或凭据文件。

## 旧库前后对照

以下五个库是本次已核实实例上的所有既有业务/验收/恢复候选库。每张 public 表的 count 和排序行摘要 MD5 前后相同，旧 migration revision 也相同；摘要是数据库端计算，不输出私人正文。count 单独不证明内容相同，所以额外核对行摘要。其他停止的旧 RAG 实例/公开评测 clone 未启动或查询，其行数仍标 NOT RUN，保护证据为容器/端口/挂载/状态一致，不能把上述数字推广到所有历史实例。

| 旧数据库 | Document | Version | Chunk | Embedding | Conversation | Answer Evidence / 引用 |
|---|---:|---:|---:|---:|---:|---:|
| `rag` | 6 → 6 | 7 → 7 | 47 → 47 | 47 → 47 | 92 → 92 | 158 → 158 |
| `rag_acceptance_task2_20261001_021151` | 38 → 38 | 50 → 50 | 36 → 36 | 21 → 21 | 7 → 7 | 17 → 17 |
| `rag_acceptance_integration01_20261001_100522` | 33 → 33 | 35 → 35 | 69 → 69 | 69 → 69 | 42 → 42 | 64 → 64 |
| `rag_acceptance_real32_20261001` | 5 → 5 | 5 → 5 | 46 → 46 | 46 → 46 | 30 → 30 | 53 → 53 |
| `rag_restore_candidate_20261002_task15` | 33 → 33 | 35 → 35 | 69 → 69 | 69 → 69 | 42 → 42 | 64 → 64 |


这里 Citation 的可观测持久化计数使用 answer_evidence，不虚构独立 Citation 表。旧库 count/摘要没有因创建新库或约束探针而变化。

## 公开与历史资产保护

复用原盘点清单，逐一复核已登记 **28,977** 个公开文件的存在、大小及 mtime，23 份 manifest/hash 记录中的非锁定 manifest SHA 保持。没有重新发现全盘资源，没有打开 corpus、问题/答案或 Locked Holdout 内容；这不是对全部 corpus 做新一轮全内容 SHA 验收。491 个原追踪文件保持原哈希，P3 报告、全部 migration 与测试夹具、Evaluation Center、旧 Gold、P3 修正证据均未改。

未来正式评测只使用 D:/RAG-Public-Bench；旧私人 Gold 仅作历史证据保留。profile 将此约定及 P7 三组同公开语料/同 RAG Context 写为阶段合同；本轮没有运行正式评分，没有改 Evaluation Center 的历史入口，也不声称 P7 三组实现已完成。P4/P7 实施时需按该公开数据合同选用实际评测入口，不能用历史 trust_v1 默认作为正式评分。

## 实际检查、遗留与停止

| 执行 | 结果/退出码 | 项目外 r1 证据 |
|---|---|---|
| Docker 元数据、恢复指定本项目 DB 容器、真实身份查询 | PASS / 0 | restore-commands.json、instance-identity.json |
| r1_create_and_verify.py | PASS / 0 | migration-command.json、migration.log、actual-schema.json、constraint-tests.json、empty-database-counts.json |
| with_clean_slate.py 默认只读模式 | PASS / 0 | profile-check-command.json、profile-guard-tests.json |
| 旧五库、公开保存清单、491 原文件、其他容器对照 | PASS / 0 | old-databases-before/after.json、public-asset-protection.json、source-protection.json、preservation-results.json |
| 自定义断言 runner JUnit（不是 pytest） | 14 PASS / 0 FAIL / 0 ERROR / 0 SKIP | cs0-r1-verification.xml；6 real DB synthetic rollback + 8 profile guards |
| P1–P3 全量回归、真实模型/OCR/VLM/付费 API | NOT RUN | 未重跑，历史 SKIP/FAIL 仍保留 |

未实现的命令不得报告通过。首轮只读元数据采集 exit 1 的断言失败也保留，原 stderr 未保存，因此原因 UNKNOWN；后续按独立 JSON 列读取 exit 0，没有抹去失败。

**P3 仍为 CHECKS_PASSED / NEEDS_OWNER_REVIEW**。本次补齐新空库真实 DDL 的这一部分证据，不等同于真实端到端摄取/pgvector检索/并发激活验收；Embedding 输入容量、无静默截断与真实模型/OCR/VLM 未验证，**依赖升级影响未独立验证**。没有模拟向量生成或 paid API 请求。极小公开文本完整摄取仍为 NOT_RUN_PROVIDER_REQUIRED。

本轮只允许四个项目文件进入 CS0 独立本地提交：本报告、另一份 CS0 报告、profile.json 与 with_clean_slate.py。提交信息 `chore(cs0): establish clean public benchmark baseline`。Git 空白/范围检查、最终哈希及提交 SHA/父提交/工作树状态见 r1 最终 manifest 和 commit-receipt。不 Push、Merge、Tag、不进入 P4。

**DELETE_APPROVAL_REQUIRED**；物理删除 0，执行清单为空。旧库与 CAS 的备份/恢复及历史依赖尚不足以批准退休，所有保留/UNKNOWN 项继续保护，不因新库成功自动删除旧数据。

完整外部证据目录：`D:\RAG-CleanSlate\cs0_20261008t072656z_352f705b\reports\r1`。上轮三个 JSON 保留原字节；本轮对应版本及旧库对照在 r1 子目录，避免覆盖历史失败记录。

---

# 以下为原 CS0_BLOCKED 报告全文（历史记录）

# CS0 — Clean-slate 实施与验证记录

状态：**CS0_BLOCKED**。本次完成只读盘点与报告，没有完成环境切换。

## 目标与实际改动

基线 `3262aede955536d473c5edb1750b9eff4d30fcfc`，分支 `codex/local-first-rag-v1-20260930`，入场工作树干净。本轮项目变更仅 docs/audits/cs0-clean-slate-inventory.md 与本报告；外部证据目录 `D:\RAG-CleanSlate\cs0_20261008t072656z_352f705b\reports`。不修改 P3 算法、migration、scope/profile/版本/引用语义，也不创建任何旧资源删除入口。

P3 仍为 **CHECKS_PASSED / NEEDS_OWNER_REVIEW**。Embedding 模型容量与真实 DB 验收未完成；**依赖升级影响未独立验证**。CS0 不将 P3 升级为已验收。

## 实际命令与结果

| 命令/检查 | 实际结果 | 退出码 |
|---|---|---:|
| git status --short --untracked-files=all（入场） | 空，干净 | 0 |
| git rev-parse HEAD | 3262aede955536d473c5edb1750b9eff4d30fcfc | 0 |
| git branch --show-current | codex/local-first-rag-v1-20260930 | 0 |
| git log -1 --oneline | 3262aed feat: add adaptive parent-child chunking and index isolation | 0 |
| docker ps -a --format '{{json .}}' | Docker Linux Engine named pipe 不存在 | 1 |
| 已知 55432/25437/25438 socket.connect，timeout=1.5 秒 | 各 TimeoutError，无 SQL/认证 | 调查脚本 0；连通性 FAIL |
| .venv/Scripts/python.exe -B var/reports/cs0/collect_blocked_inventory.py | 只读盘点、SHA/metadata 核验、写报告；断言失败则非零 | 实际退出码见外部 collector-exit 与工具回执 |

未实现的命令不得报告通过。没有重跑 P1–P3 测试，已有 P3 387 定向 PASS / 广泛 1097 PASS、9 FAIL、127 SKIP、20 ERROR 只是保留的历史证据，不是本轮执行。

## Migration、空基线与切换

| 项目 | 当前状态与证据 |
|---|---|
| 新库 | NOT RUN；仅拟定唯一名 `rag_clean_dev_20261008t072656z_352f705b`，未查询 pg_database，不宣称已证明不存在 |
| 完整 migration 链 | 静态 AST 确认 16 个 revision、单 head 0016_parent_child_chunks；含 0014/0015/0016；没有连接 DB/apply |
| 实际迁移版本 | UNKNOWN；真实 DDL、Parent-Child FK/Check、列与 Embedding profile 约束 NOT RUN |
| 新 Storage | 拟定 `D:\RAG-CleanSlate\cs0_20261008t072656z_352f705b\storage`，入场不存在，未创建或复用旧 CAS |
| 空数据计数 | KnowledgeBase/Document/Version/Chunk/Embedding/Conversation/Answer Evidence 全部 NOT RUN，不写成 0 |
| 旧库计数前后 | UNKNOWN / UNKNOWN，未验证相等；本轮 SQL 请求 0，不等价于已证明其他消费者没有写入 |
| 配置切换 | NOT RUN；无可验证新库前不写指向旧库或不可用目标的配置。原 Settings 默认、Compose、.env 均未改，现有服务尚未退休 |
| 上传/检索/profile/citation 运行时隔离 | NOT RUN；源码 P3 过滤保持原哈希，但不能替代新环境验证 |
| 极小公开样本摄取 | NOT_RUN_PROVIDER_REQUIRED；未调用模型，不用模拟向量冒充真实验收 |
| 公开目录与夹具 | metadata/非锁定 manifest 哈希及追踪文件哈希前后复核，结果见 final-checks.json；没有删除 |
| 外部收费 API、ECS、Langfuse、ledger | 0 次请求；未连接、未更改 |

## 交付与恢复条件

项目外三份必需 JSON：cs0-resource-manifest.json、cs0-database-verification.json、cs0-deletion-candidates.json；另含 preflight-observations、命令/Git 检查与最终哈希。

新隔离环境、migration、空数据、实际配置隔离均未完成，因此不满足 CS0_PASS，也不满足附件第十二节创建本地提交的条件。**本轮不 commit、不 push、不 merge、不 tag、不物理删除、不进入 P4。** 删除状态 DELETE_APPROVAL_REQUIRED，实际候选为空，所有 UNKNOWN/KEEP 项继续保护。

恢复入口：先恢复已授权实例和非敏感身份信息；复核 HEAD/两个报告之外是否有并行改动，再补旧资源/连接计数及当前消费者，证明新库不存在后创建，应用现有完整 migration 链并验证真实 DDL。如果迁移失败，保存真实错误并 CS0_BLOCKED，不修改 P3 migration。
