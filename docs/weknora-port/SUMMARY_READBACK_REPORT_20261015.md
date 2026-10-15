# Summary 引用回读回归修复

基线：`4f3aedc2e18a12920ed73ef7d6cbb566ff38f6f0`，独立 E 盘工作树；原工作区未写入。

## 目标与实际修改

`backend/weknora_port/documents.py::read_source_chunk` 的版本检查使正常 summary 返回 404。`document_profile.py` 创建的 summary 只有 `origin=model_generated_summary`、`upstream_parent_link=true`，通过 `parent_chunk_id` 关联原始 text；`storage/knowledge.py::create_chunks` 原样保存 metadata。固定上游 `Tencent/WeKnora@3e8b0bfc80b845b2d4b2ed683994748741450a97` 的 `internal/application/service/knowledge_process.go::ProcessSummaryGeneration`（1517–1518）同样保留 summary 类型、关联 `textChunks[0].ID`。

唯一运行时代码修改为 `documents.py`：同一 SQL LEFT JOIN 活跃的原始 text 父片段，同时绑定 tenant/KB/document。summary 必须有既定生成标记、真实父片段和 completed 文档，其父版本必须非空且等于当前文档版本。summary 若已明确携带版本，也必须与父版本一致；只有未携带版本时使用经核验的父版本作为回读 DTO 的版本。没有补写 metadata，没有把 summary 内容替换成父原文，没有放宽普通片段的版本要求。Owner 新增的安全 DTO 白名单保持。

新增 `test_summary_source_readback.py`、`test_summary_source_readback_pg.py`。本轮没有修改 summary 生成、路由、模型、权限、费用或 CLI 接线。

## 命令、退出码与证据

全部命令在本工作树执行，解释器为 `E:\RAG quention\.venv\Scripts\python.exe -B`。前四项经 `docs/weknora-port/evidence/SUMMARY_READBACK_20261015/run-check.py` 包装；完整 pytest argv、E 盘临时目录、stdout/stderr、退出码与 JUnit 均保存在同目录相应前缀文件。没有覆盖历史证据。

| 命令参数（接在解释器之后） | 退出码 | 实际结果 |
|---|---:|---|
| `docs/weknora-port/evidence/SUMMARY_READBACK_20261015/run-check.py red tests/weknora_port/test_summary_source_readback.py` | 1 | 修复前 1 failed / 11 passed；正常 summary 被拒绝 |
| `docs/weknora-port/evidence/SUMMARY_READBACK_20261015/run-check.py green tests/weknora_port/test_summary_source_readback.py tests/weknora_port/test_documents.py` | 0 | 16 passed |
| `docs/weknora-port/evidence/SUMMARY_READBACK_20261015/run-check.py pg --pg-env tests/weknora_port/test_summary_source_readback_pg.py` | 0 | 1 passed；实际 ASGI/PG/Redis，闭合 MockTransport |
| `docs/weknora-port/evidence/SUMMARY_READBACK_20261015/run-check.py related tests/weknora_port/test_document_profile.py tests/weknora_port/test_entrypoint_migration.py tests/weknora_port/test_development_entry.py` | 0 | 29 passed |
| `docs/weknora-port/evidence/SUMMARY_READBACK_20261015/compare-readers.py` | 0 | 同一 row driver：341dec08 SUCCESS，4f3aedc2 REJECTED，修复后 SUCCESS |

`reader-comparison.json` 绑定三份实际 reader 源码哈希；这是离线函数对照，不是三个版本的实际服务重跑。`pg.pg.json` 记录实际生成的 summary、原始父片段、版本、13 个 HTTP 404 负例和真实回答引用回读 200。负例包含缺失/自指父片段、跨 tenant/KB/document、父类型不符、已删除父片段、旧父版本、普通 text 缺失版本、summary 明确错误版本，以及 pending/processing/failed 文档。恢复并逐行比较了本轮新建 chunk 数据；历史 chunk 未修改。为使引用确实指向 summary，仅关闭本轮新建文档其他片段的 embedding 候选。

`git diff --cached --check` 最终完整退出码 0，见 `diff-check.json`。初次检查把原样保存的 CRLF launcher 误报为行尾空白（exit 2）；仅在新增证据目录声明 `-text` 与 `cr-at-eol` 后通过，未改写已运行脚本或原始输出字节。

`infrastructure-readonly.json` 记录实际命令：Docker inspect、pg_controldata、Redis INFO 均 exit 0。当前名称为 `eviroute-rag-postgres` / `eviroute-rag-redis`；PG 物理标识 `7696088403504697382`、镜像与 loopback 56423/56424 和既有独立环境一致。没有启停、重建或更改容器配置。测试连接仍执行既有物理标识校验，Redis 使用本次实际 run_id。

## 结果与边界

本修复：`IMPLEMENTED / OFFLINE_PASS / ISOLATED_INTEGRATION_PASS`。模拟模型行为与真实存储/ASGI 入口分别标注，真实供应商请求 0。`REAL_PROVIDER_PASS / CUTOVER_PASS`：NOT RUN；浏览器本轮 NOT RUN，没有重新执行历史浏览器任务。没有 push/tag/release、旧业务数据库操作、UNKNOWN 释放或权限变更。

历史浏览器证据没有同时生成的源码哈希清单。该限制仍成立，本轮当前源码绑定不能追溯证明当时源码。没有补造历史哈希或把新增测试当成历史浏览器复验。

`protected-before.json` 记录 `.serena/project.yml` 的既有未提交修改及四个受保护未跟踪文件；交付前逐字节哈希复核一致，均不暂存。新增证据和源文件哈希另见 `source-binding.json`。

CLI 权限接线仅完成只读分析，见 [AUTHORITY_BOOTSTRAP_ASSESSMENT_20261015.md](AUTHORITY_BOOTSTRAP_ASSESSMENT_20261015.md)。总体 P0–P8、真实模型与切换验收不因本修复而宣称通过。没有提出或批准新延期。
