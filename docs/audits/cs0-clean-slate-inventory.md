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

# CS0 — 旧资源只读盘点与删除候选

状态：**CS0_BLOCKED / DELETE_APPROVAL_REQUIRED**。当前没有可执行删除对象。

## 任务与基线

- 时间：2026-10-08 15:27:09 +0800（Asia/Shanghai）。运行 ID：`cs0_20261008t072656z_352f705b`。
- HEAD：`3262aede955536d473c5edb1750b9eff4d30fcfc`；分支：`codex/local-first-rag-v1-20260930`；git log -1：`3262aed feat: add adaptive parent-child chunking and index isolation`。入场工作树干净。
- 本次仅调查本 RAG 项目明确路径；不扫描整盘、全部数据库，不访问 ECS、凭据、模型或调用账本。
- 项目写入白名单仅本报告及 cs0-clean-slate-implementation.md。运行时 JSON 在项目外 `D:\RAG-CleanSlate\cs0_20261008t072656z_352f705b\reports`。未创建新数据库或新 Storage，未改变旧配置。
- Codex 最近 30 个任务中没有观察到其他活跃 RAG 任务；另一活跃项目 D:/studyplan 排除。Dots 和外部进程是否写数据 UNKNOWN；没有强制并发锁，执行方式 MANUALLY_SUPERVISED_TRIAL。未派发 Worker。

## 阻断证据与调查边界

`docker ps -a --format '{{json .}}'` 实际退出 1，Docker Linux Engine 管道不存在。对源码/历史报告明确记录的 loopback:55432、25437、25438 分别作一次 1.5 秒 socket connect，均 TimeoutError；没有发送 SQL 或尝试认证。这不能证明数据库不存在或数据为空，只能证明本次无法访问和确认安全实例。

附件第五节要求“没有可安全使用的隔离 PostgreSQL 时，停止并报告，不得自行操作其他服务的数据库”。因此没有启动 Docker、选用其他服务、读 .env 或连接文件，也没有根据旧报告推定当前数据库身份。仅完成报告要求的安全只读盘点。

## 资源分类

| resource_id | 分类 | 精确资源或受限定位 | 当前消费者/引用/恢复状态 |
|---|---|---|---|
| `postgres-loopback-55432` | `UNKNOWN` | {'host': 'loopback', 'port': 55432, 'database_name': 'rag'} | UNKNOWN / 保留 |
| `postgres-loopback-25437` | `UNKNOWN` | {'host': 'loopback', 'port': 25437, 'database_name': 'UNKNOWN; rag_eval_trust_* is the permitted historical experiment pattern'} | UNKNOWN / 保留 |
| `postgres-loopback-25438` | `UNKNOWN` | {'host': 'loopback', 'port': 25438, 'database_name': 'rag'} | UNKNOWN / 保留 |
| `path-2c5241d8f0c5` | `KEEP_SHARED_RESOURCE` | C:\Users\22088\.codex\worktrees\rag-retrieval-opt-round1\RAG quention\var\storage | UNKNOWN / 保留 |
| `path-a908491d4bf7` | `KEEP_SHARED_RESOURCE` | E:\RAG quention\var\storage | UNKNOWN / 保留 |
| `path-9f8ea8ebafd3` | `KEEP_HISTORICAL_EVIDENCE` | C:\Users\22088\.codex\worktrees\rag-retrieval-opt-round1\RAG quention\var\backups | UNKNOWN / 保留 |
| `path-e6f9852e4e64` | `KEEP_HISTORICAL_EVIDENCE` | E:\RAG quention\var\backups | UNKNOWN / 保留 |
| `public-bench` | `KEEP_PUBLIC_DATA` | D:\RAG-Public-Bench | UNKNOWN / 保留 |
| `backend-code` | `KEEP_HISTORICAL_EVIDENCE` | C:\Users\22088\.codex\worktrees\rag-retrieval-opt-round1\RAG quention\backend | UNKNOWN / 保留 |
| `eval-center` | `KEEP_HISTORICAL_EVIDENCE` | C:\Users\22088\.codex\worktrees\rag-retrieval-opt-round1\RAG quention\eval_center | UNKNOWN / 保留 |
| `evaluation-gold` | `KEEP_HISTORICAL_EVIDENCE` | C:\Users\22088\.codex\worktrees\rag-retrieval-opt-round1\RAG quention\evaluations | UNKNOWN / 保留 |
| `migrations` | `KEEP_HISTORICAL_EVIDENCE` | C:\Users\22088\.codex\worktrees\rag-retrieval-opt-round1\RAG quention\alembic\versions | UNKNOWN / 保留 |
| `audit-reports` | `KEEP_HISTORICAL_EVIDENCE` | C:\Users\22088\.codex\worktrees\rag-retrieval-opt-round1\RAG quention\docs\audits | UNKNOWN / 保留 |
| `test-fixtures` | `KEEP_HISTORICAL_EVIDENCE` | C:\Users\22088\.codex\worktrees\rag-retrieval-opt-round1\RAG quention\backend\tests\fixtures | UNKNOWN / 保留 |
| `prior-p3-evidence` | `KEEP_HISTORICAL_EVIDENCE` | C:\Users\22088\.codex\worktrees\rag-retrieval-opt-round1\RAG quention\var\reports\p3 | UNKNOWN / 保留 |
| `all-runtime-reports` | `KEEP_HISTORICAL_EVIDENCE` | C:\Users\22088\.codex\worktrees\rag-retrieval-opt-round1\RAG quention\var\reports | UNKNOWN / 保留 |
| `pytest-residue` | `KEEP_HISTORICAL_EVIDENCE` | {'root': 'C:\\Users\\22088\\.codex\\worktrees\\rag-retrieval-opt-round1\\RAG quention\\var', 'exact_directory_names': ['p1-baseline-source', 'p1-pytest-regression-1', 'p1-pytest-regression-final', 'p1-pytest-targeted-2', 'p1-pytest-targeted-3', 'p1-pytest-targeted-final', 'p1-pytest-targeted-final2', 'p1-test-storage', 'p1-tmp-runtime', 'p2-baseline-source', 'p2-pdf-closure-new', 'p2-pdf-closure-new-repair', 'p2-pdf-closure-original', 'p2-pdf-closure-regression', 'p2-pdf-closure-storage', 'p2-pdf-closure-temp', 'p2-pytest-baseline', 'p2-pytest-final', 'p2-pytest-first', 'p2-pytest-new', 'p2-pytest-regression', 'p2-pytest-targeted', 'p2-temp', 'p2-test-storage', 'p3-pytest-baseline', 'p3-pytest-final-regression', 'p3-pytest-final-targeted', 'p3-pytest-final2-regression', 'p3-pytest-final2-targeted', 'p3-pytest-regression-1', 'p3-pytest-targeted-1', 'p3-pytest-targeted-final', 'p3-pytest-verification-regression', 'p3-pytest-verification-targeted', 'p3-review-final-regression', 'p3-review-final-targeted', 'p3-review-final2-regression', 'p3-review-final2-targeted', 'p3-review-green', 'p3-review-red', 'p3-temp', 'p3-test-storage']} | UNKNOWN / 保留 |
| `compose-logical-volume-rag-db` | `KEEP_SHARED_RESOURCE` | {'compose_file': 'deploy/compose.yml', 'logical_name': 'rag-db', 'actual_volume_name': 'UNKNOWN'} | UNKNOWN / 保留 |
| `compose-logical-volume-rag-storage` | `KEEP_SHARED_RESOURCE` | {'compose_file': 'deploy/compose.yml', 'logical_name': 'rag-storage', 'actual_volume_name': 'UNKNOWN'} | UNKNOWN / 保留 |
| `historical-eval-container` | `UNKNOWN` | rag-eval-trust0928-db-clone-20260929t091523z | UNKNOWN / 保留 |
| `ecs-evaluation-center` | `KEEP_SHARED_RESOURCE` | /srv/rag-eval | UNKNOWN / 保留 |
| `cloud-sqlite` | `KEEP_SHARED_RESOURCE` | /srv/rag-eval/experiments/registry.sqlite3 | UNKNOWN / 保留 |
| `langfuse-history` | `KEEP_SHARED_RESOURCE` | UNKNOWN; external resource, not queried | UNKNOWN / 保留 |
| `provider-ledgers` | `KEEP_SHARED_RESOURCE` | UNKNOWN; not queried | UNKNOWN / 保留 |
| `embedding-model-files` | `KEEP_SHARED_RESOURCE` | UNKNOWN; not queried | UNKNOWN / 保留 |
| `credentials` | `KEEP_SHARED_RESOURCE` | UNKNOWN; never opened | UNKNOWN / 保留 |

数据库项的 name、host、归属证据分别保存；host 仅写 loopback。历史开发库 rag:55432、只读实验 rag:25438 来自源码；25437 是历史公开评测 clone，当前数据库名、连接、大小、Document/Version/Chunk/Embedding/Citation 数量均 UNKNOWN。未遍历 PostgreSQL database catalog；共享数据库及当前服务实际使用库 UNKNOWN。

CAS 调查仅目录 metadata，不读取私人上传内容。ContentAddressedStorage（backend/app/adapters/storage.py）以 objects/SHA 前缀保存不可变对象；Settings.storage_root/from_env（backend/app/config.py）与 build_container（backend/app/bootstrap.py:84）是配置/消费者，实际进程 cwd、环境及 DB storage_key 引用尚未核实。对象可能由多个版本或 proof 共享，不能按目录年代批准删除。OCR/caption/缓存是否独立落盘的运行时归属 UNKNOWN。

## 公开数据、Gold 与历史证据保护

`D:/RAG-Public-Bench` 实际存在：metadata 共 28977 个文件、3763916907 字节；识别 23 份 manifest/hash 文件。仅哈希非 Locked/Holdout manifest 字节，未打开语料、问题/答案或 Locked Holdout 内容。前后 metadata/manifest 哈希复核见外部 final-checks；没有把这些检查冒称为全部 corpus 与 manifest 声明 SHA 的一致性验收。

backend、eval_center、evaluations、迁移、测试夹具、P0/P1/P2/P3 报告及 var/reports 历史证据全部保留。旧私人 Gold 是否属于真实业务及其历史依赖不能按文件名推定；eval_center/runner.py:275 仍引用 evaluations/trust_v1，旧测试/评测资产不能删除。未来 P7 应单独明确公开语料白名单和相同上下文三组合同；本轮没有切换评分入口或删除旧 Gold。

入场追踪文件哈希共 491 项，P3 报告 SHA：`985b9933e18d0c0666f7c6f28ae7652157afe0d480b0597b1ada87f81c2365f9`；冻结 PDF 与其他夹具保留，前轮证据 ZIP SHA：`9fd2150766487417427c1a007d3c677f23d2040b7554659ef38bfb05fbb6d6ca`。保护项完整清单及目录实际大小见 cs0-resource-manifest.json。

## 删除清单与补证

**DELETE_APPROVAL_REQUIRED**，但 DELETE_AFTER_APPROVAL 和 executable_delete_list 均为空。UNKNOWN 和 KEEP 项不是请求批准删除的对象。需要恢复已授权实例后补齐当前对象身份、消费者/连接、CAS 反向引用、备份及恢复能力，并完成新环境切换，才能形成实际删除候选。

本轮没有 DROP/TRUNCATE、文件删除、Docker prune/down -v 或服务停止。最小补证：用户恢复已授权隔离 PostgreSQL/Docker 的可用状态，提供非敏感实例/容器标识和端口；凭据仍由用户私下提供给运行环境。恢复后先核对身份与旧计数，再确认唯一新库不存在、创建/迁移新库并验证空基线。无需扩大到其他项目。
